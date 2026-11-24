"""Which documents a gateway filing carries, and under what names.

What is being protected: the Bank of Ghana receives the return's Excel WITH the
template's live formulas, not a values-only copy, while a copy the bank's officers
signed always goes. The bytes are read through the archived revision (stubbed here);
this module pins the selection and naming.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.attestation.public import RegulatoryArtifactVersion
from app.reporting.models.packages import RegulatoryPackage, RegulatoryPackageArtifact
from app.reporting.service import gateway_documents

PACKAGE_ID = uuid4()


def _package() -> RegulatoryPackage:
    return RegulatoryPackage(
        id=PACKAGE_ID,
        organization_id="OR-TEST0001",
        bank_id="BK-TEST0001",
        return_code="CAR-RWA",
        reporting_date=date(2026, 6, 30),
    )


def _artifact(kind: str, path: str) -> RegulatoryPackageArtifact:
    return RegulatoryPackageArtifact(
        organization_id="OR-TEST0001",
        package_id=PACKAGE_ID,
        kind=kind,
        object_path=f"bog_returns/2026-06-30/{PACKAGE_ID}/{path}",
        checksum_sha256="0" * 64,
        size_bytes=10,
    )


def _signed(kind: str, path: str) -> RegulatoryArtifactVersion:
    version = RegulatoryArtifactVersion(
        organization_id="OR-TEST0001",
        package_id=PACKAGE_ID,
        kind=kind,
        object_path=f"bog_returns/2026-06-30/{PACKAGE_ID}/{path}",
    )
    version.id = uuid4()
    return version


@pytest.fixture(autouse=True)
def _storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each document's bytes are its own path, so the test can see which file went."""

    def archived(*_args: object, **kwargs: Any) -> RegulatoryArtifactVersion:
        version = RegulatoryArtifactVersion(
            organization_id="OR-TEST0001",
            package_id=PACKAGE_ID,
            kind=kwargs["kind"],
            object_path=kwargs["object_path"],
        )
        version.id = uuid4()
        return version

    paths: dict[object, str] = {}

    def read(_db: object, _ctx: object, _bank: str, version_id: object, _storage: object) -> Any:
        return None, paths[version_id].encode()

    def remember(*args: object, **kwargs: Any) -> RegulatoryArtifactVersion:
        version = archived(*args, **kwargs)
        paths[version.id] = version.object_path
        return version

    original = gateway_documents._version_of  # pyright: ignore[reportPrivateUsage]

    def version_of(db: Any, ctx: Any, package: Any, artifact: Any) -> RegulatoryArtifactVersion:
        if isinstance(artifact, RegulatoryArtifactVersion):
            paths[artifact.id] = artifact.object_path
            return artifact
        return original(db, ctx, package, artifact)

    monkeypatch.setattr(gateway_documents.artifact_versions, "archived_version", remember)
    monkeypatch.setattr(gateway_documents.artifact_versions, "read_version_bytes", read)
    monkeypatch.setattr(gateway_documents, "_version_of", version_of)
    monkeypatch.setattr(gateway_documents, "get_storage_client", object)


def _sent(artifacts: list[Any]) -> list[tuple[str, str]]:
    documents = gateway_documents.filed_documents(
        None,  # pyright: ignore[reportArgumentType]
        None,  # pyright: ignore[reportArgumentType]
        _package(),
        artifacts,
    )
    return [(document.name, document.content.decode().rsplit("/", 1)[-1]) for document in documents]


def test_the_formula_workbook_is_the_excel_the_regulator_receives() -> None:
    sent = _sent(
        [
            _artifact("pdf", "BSD5A.pdf"),
            _artifact("xlsx", "BSD5A.xlsx"),
            _artifact("xlsx_working", "BSD5A.working.xlsx"),
            _artifact("csv", "BSD5A.zip"),
        ]
    )
    assert sent == [
        ("CAR-RWA_2026-06-30.pdf", "BSD5A.pdf"),
        ("CAR-RWA_2026-06-30.xlsx", "BSD5A.working.xlsx"),
        ("CAR-RWA_2026-06-30.zip", "BSD5A.zip"),
    ]


def test_a_signed_values_only_copy_still_goes_beside_the_workbook() -> None:
    sent = _sent(
        [
            _signed("xlsx", "BSD5A.signed.xlsx"),
            _artifact("pdf", "BSD5A.pdf"),
            _artifact("xlsx_working", "BSD5A.working.xlsx"),
        ]
    )
    assert sent == [
        ("CAR-RWA_2026-06-30_signed_values.xlsx", "BSD5A.signed.xlsx"),
        ("CAR-RWA_2026-06-30.pdf", "BSD5A.pdf"),
        ("CAR-RWA_2026-06-30.xlsx", "BSD5A.working.xlsx"),
    ]


def test_without_a_formula_workbook_the_values_only_excel_goes() -> None:
    sent = _sent([_artifact("xlsx", "LMT.xlsx"), _artifact("pdf", "LMT.pdf")])
    assert sent == [("CAR-RWA_2026-06-30.xlsx", "LMT.xlsx"), ("CAR-RWA_2026-06-30.pdf", "LMT.pdf")]


def test_the_word_working_draft_never_goes() -> None:
    assert _sent([_artifact("docx_working", "ICAAP.docx"), _artifact("pdf", "ICAAP.pdf")]) == [
        ("CAR-RWA_2026-06-30.pdf", "ICAAP.pdf")
    ]
