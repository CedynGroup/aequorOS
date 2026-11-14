"""The read-only exporter sees and takes away finished returns — and nothing else.

One EXPORTER binding (Regulatory Reporting / restricted) admits every export
surface — package export, artifact download, archived revision download and the
downtime email bundle with its instructions preview — because those gates ask
for family visibility plus REG/restricted EXPORT and nothing more. The same
binding refuses every act that could change, approve, sign or transmit the
return, before any side effect. The bundle never arrives by migration,
provisioning or invitation; only an administrator's composer grant creates it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    ModuleScope,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    PackageSignatureRecipient,
    RegulatoryPackage,
    RegulatoryPackageArtifact,
    User,
)
from app.reporting.service import workflow as reporting_workflow
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.reporting.api.test_package_authorization_integration import (
    _add_bank,
    _artifact,
    _audit_events,
    _grant,
    _package,
)
from tests.support.helpers import ORG_1, USER_1, headers
from tests.support.inmemory_storage import InMemoryStorageClient

SIBLING_BANK_ID = "BK-EXPS0001"


@pytest.fixture(autouse=True)
def _seed(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _exporter_grant(
    *,
    module_scope: ModuleScope = ModuleScope.REGULATORY,
    sensitivity_scope: SensitivityScope = SensitivityScope.RESTRICTED,
    institution_id: str | None = SAMPLE_BANK_ID,
    user_id: UUID = USER_1,
) -> int:
    return _grant(
        role_bundle=RoleBundle.EXPORTER,
        module_scope=module_scope,
        sensitivity_scope=sensitivity_scope,
        institution_id=institution_id,
        user_id=user_id,
    )


def _fake_export(
    db: Session, ctx: TenantContext, package: RegulatoryPackage, kind: str
) -> RegulatoryPackageArtifact:
    _ = ctx
    row = RegulatoryPackageArtifact(
        organization_id=package.organization_id,
        package_id=package.id,
        kind=kind,
        object_path=f"fixture/{package.id}/{package.return_code}.{kind}",
        checksum_sha256="0" * 64,
        size_bytes=1,
    )
    db.add(row)
    db.flush()
    return row


def test_an_exporter_reads_and_takes_away_a_finished_ordinary_return(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One EXPORTER row opens the package and all four export surfaces."""
    monkeypatch.setattr(reporting_workflow, "_resolve_exporter", lambda: _fake_export)
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    artifact_id, version_id = _artifact(package_id, storage_engine)
    authv = _exporter_grant()
    auth = headers(roles=("viewer",), authorization_version=authv)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"

    package = db_client.get(f"{base}", headers=auth)
    assert package.status_code == 200, package.text

    generated = db_client.post(f"{base}/export", headers=auth, params={"kind": "xlsx"})
    assert generated.status_code == 201, generated.text
    # The fake seam mints no archived revision; leave only the seeded PDF so the
    # email bundle resolves every document it lists.
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        session.execute(
            delete(RegulatoryPackageArtifact).where(
                RegulatoryPackageArtifact.package_id == package_id,
                RegulatoryPackageArtifact.kind == "xlsx",
            )
        )
        session.commit()

    download = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifacts/{artifact_id}/download",
        headers=auth,
    )
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"LCR-NSFR:pdf:")

    revision = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifact-versions/{version_id}/download",
        headers=auth,
    )
    assert revision.status_code == 200, revision.text
    assert revision.content == download.content

    instructions = db_client.get(f"{base}/email-fallback-instructions", headers=auth)
    assert instructions.status_code == 200, instructions.text
    assert db_client.get(f"{base}/email-fallback.eml", headers=auth).status_code == 200


def test_an_exporter_takes_away_an_icaap_return_it_may_see(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ICAAP visibility stays Capital/confidential; taking the file stays REG EXPORT.

    The exporter row alone cannot even see an ICAAP return. Beside a read-only
    Capital/confidential VIEWER row — still no preparation — the same actor
    downloads the artifact and the archived revision.
    """
    monkeypatch.setattr(reporting_workflow, "_resolve_exporter", lambda: _fake_export)
    package_id = _package(family="icaap", return_code="ICAAP-REPORT")
    artifact_id, version_id = _artifact(package_id, storage_engine)

    hidden = headers(
        roles=("viewer",),
        authorization_version=_exporter_grant(),
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}", headers=hidden
        ).status_code
        == 404
    )

    authv = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    auth = headers(roles=("viewer",), authorization_version=authv)
    for path in (
        f"regulatory-artifacts/{artifact_id}/download",
        f"regulatory-artifact-versions/{version_id}/download",
    ):
        served = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/{path}", headers=auth)
        assert served.status_code == 200, path
        assert served.content.startswith(b"ICAAP-REPORT:pdf:")


def _deny_state(db_client: TestClient, package_id: UUID) -> dict[str, Any]:
    """The observable state a refused exporter must leave exactly as it was."""
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        package = session.get(RegulatoryPackage, package_id)
        assert package is not None
        artifacts = list(
            session.scalars(
                select(RegulatoryPackageArtifact.id).where(
                    RegulatoryPackageArtifact.organization_id == ORG_1,
                    RegulatoryPackageArtifact.package_id == package_id,
                )
            )
        )
        recipients = list(
            session.scalars(
                select(PackageSignatureRecipient.id).where(
                    PackageSignatureRecipient.organization_id == ORG_1,
                    PackageSignatureRecipient.package_id == package_id,
                )
            )
        )
        return {
            "status": package.status,
            "artifacts": artifacts,
            "recipients": recipients,
            "exported_events": _audit_events(str(package_id), "regulatory_package.exported"),
            "downloaded_events": _audit_events(
                str(package_id), "regulatory_artifact_version.downloaded"
            ),
        }


@pytest.mark.parametrize(
    ("path", "body"),
    [
        pytest.param("validate", None, id="prepare-checks"),
        pytest.param("request-approval", {}, id="edit"),
        pytest.param(
            "request-resubmission",
            {"reason": "exporter attempt"},
            id="resubmission-edit",
        ),
        pytest.param("decide-approval", {"action": "approved"}, id="approve"),
        pytest.param("submit", {}, id="transmit"),
        pytest.param("poll", {}, id="transmission-poll"),
        pytest.param(
            "attestation/certify",
            {
                "signing_role": "preparer",
                "authorization_token": "not-a-real-token",
                "expected_certification_digest": "0" * 64,
            },
            id="sign",
        ),
    ],
)
def test_an_exporter_cannot_change_approve_sign_or_transmit(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every mutating surface answers 403 with the shared denial, and no effect."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    artifact_id, _version_id = _artifact(package_id, storage_engine)
    authv = _exporter_grant()
    auth = headers(roles=("viewer",), authorization_version=authv)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"

    before = _deny_state(db_client, package_id)
    if body is None:
        response = db_client.post(f"{base}/{path}", headers=auth)
    else:
        response = db_client.post(f"{base}/{path}", headers=auth, json=body)
    assert response.status_code == 403, (path, response.text)
    assert b"LCR-NSFR:pdf:" not in response.content

    after = _deny_state(db_client, package_id)
    assert after == before
    assert after["artifacts"] == [artifact_id]

    # The download surfaces stay untouched too: the refusal minted nothing and
    # fetched no bytes.
    download = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-artifacts/{artifact_id}/download",
        headers=auth,
    )
    assert download.status_code == 200
    assert download.content.startswith(b"LCR-NSFR:pdf:")


def test_an_exporter_row_is_never_issued_without_the_exact_scope(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
) -> None:
    """Wrong module, wrong classification, sibling bank or no grant hides the return.

    Visibility comes first: a caller who may not know the return exists is told
    nothing (404), exactly like every other principal. One actor per case —
    every grant invalidates the holder's earlier sessions, so a single actor
    could not hold four separate scope sentences for comparison.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    artifact_id, version_id = _artifact(package_id, storage_engine)
    _add_bank(SIBLING_BANK_ID, organization_id=ORG_1)
    artifact_paths = (
        f"regulatory-artifacts/{artifact_id}/download",
        f"regulatory-artifact-versions/{version_id}/download",
    )
    actors: dict[str, UUID] = {}
    for name in ("confidential-only", "capital-only", "sibling-bank", "no-grant"):
        user = uuid4()
        with get_sessionmaker()() as session:
            session.info["organization_id"] = ORG_1
            session.add(
                User(
                    id=user,
                    organization_id=ORG_1,
                    email=f"{name}@exporter-scope.test",
                    role="viewer",
                )
            )
            session.commit()
        actors[name] = user

    _exporter_grant(
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
        user_id=actors["confidential-only"],
    )
    _exporter_grant(module_scope=ModuleScope.CAPITAL, user_id=actors["capital-only"])
    _exporter_grant(institution_id=SIBLING_BANK_ID, user_id=actors["sibling-bank"])
    grants: dict[str, int] = {
        "confidential-only": 1,
        "capital-only": 1,
        "sibling-bank": 1,
        "no-grant": 1,
    }
    for name in ("confidential-only", "capital-only", "sibling-bank"):
        with get_sessionmaker()() as session:
            session.info["organization_id"] = ORG_1
            user = session.get(User, actors[name])
            assert user is not None
            session.refresh(user)
            grants[name] = user.authorization_version
    for name in ("confidential-only", "capital-only", "sibling-bank", "no-grant"):
        auth = headers(
            user_id=actors[name], roles=("viewer",), authorization_version=grants[name]
        )
        seen = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}", headers=auth
        )
        assert seen.status_code == 404, (name, seen.text)
        for path in artifact_paths:
            refused = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/{path}", headers=auth)
            assert refused.status_code == 404, (name, path)
            assert b"LCR-NSFR:pdf:" not in refused.content

    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        stored = session.scalars(
            select(AuditEvent.id).where(
                AuditEvent.organization_id == ORG_1,
                AuditEvent.entity_id == str(package_id),
            )
        ).all()
        assert stored == []


def test_the_migration_and_provisioning_never_issue_the_exporter(
    db_client: TestClient,
) -> None:
    """No exporter binding exists until an administrator grants one in the composer.

    The seeded surface here is everything provisioning, invitations and
    single-sign-on produce: members, owners, analysts and machine credentials —
    and not one exporter row.
    """
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        materialize_canonical_test_book(session)
        session.commit()
        rows = list(
            session.scalars(
                select(AuthorizationBinding).where(
                    AuthorizationBinding.role_bundle == RoleBundle.EXPORTER.value
                )
            )
        )
        assert rows == []


def _package_counts(package_id: UUID) -> tuple[list[UUID], list[UUID]]:
    with get_sessionmaker()() as session:
        session.info["organization_id"] = ORG_1
        artifacts = list(
            session.scalars(
                select(RegulatoryPackageArtifact.id).where(
                    RegulatoryPackageArtifact.package_id == package_id
                )
            )
        )
        events = list(
            session.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.organization_id == ORG_1,
                    AuditEvent.entity_id == str(package_id),
                )
            )
        )
        return artifacts, events


def test_the_exporter_surface_never_mints_or_writes_on_a_refusal(
    db_client: TestClient,
    storage_engine: InMemoryStorageClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package the exporter may not take away produces no artifact and no event.

    The export route's gate runs before the exporter: the seam is patched to
    fail loudly, the store refuses writes, and afterwards the artifact list and
    the audit chain are exactly what they were.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    artifact_id, _version_id = _artifact(package_id, storage_engine)
    # Only the read half of the bundle: a VIEWER row at the same scope.
    authv = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    auth = headers(roles=("viewer",), authorization_version=authv)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    assert db_client.get(base, headers=auth).status_code == 200

    def _spy_export(
        db: Session, ctx: TenantContext, package: RegulatoryPackage, kind: str
    ) -> RegulatoryPackageArtifact:
        _ = (db, ctx, package)
        pytest.fail("a principal without export authority reached the exporter")

    monkeypatch.setattr(reporting_workflow, "_resolve_exporter", lambda: _spy_export)
    monkeypatch.setattr(
        storage_engine,
        "write",
        Mock(side_effect=AssertionError("an unauthorized storage write was attempted")),
    )
    refused = db_client.post(f"{base}/export", headers=auth, params={"kind": "pdf"})
    assert refused.status_code == 403, refused.text

    artifacts, _events = _package_counts(package_id)
    assert artifacts == [artifact_id]
    assert _audit_events(str(artifact_id), "regulatory_artifact_version.downloaded") == []
