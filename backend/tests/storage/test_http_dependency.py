"""Storage construction and override wiring through the real attachment routes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from types import ModuleType
from typing import Never

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import deps
from app.core.tenancy import TenantContext
from app.icaap.api import manage_icaap_attachments, manage_icaap_supervisory_addons
from app.identity.public import Bank
from app.reporting.api import manage_package_attachments
from app.reporting.models.packages import RegulatoryPackage
from app.storage import factory
from app.storage.client import StorageClient, StorageValidationError
from app.storage.config import StorageEngineSettings, StorageRetiredError
from app.storage.deps import get_http_storage
from tests.support.inmemory_storage import InMemoryStorageClient

_BANK = "BK-SAMP0001"
_OBJECT = "00000000-0000-0000-0000-000000000001"


@dataclass(frozen=True)
class UploadCase:
    router: APIRouter
    path: str
    dependency: Callable[[], StorageClient]
    service: ModuleType
    operation: str
    storage_argument: int
    file_field: str
    data: dict[str, str]


_CASES = [
    UploadCase(
        manage_icaap_attachments.router,
        f"/banks/{_BANK}/icaap/cycles/{_OBJECT}/attachments",
        manage_icaap_attachments.get_icaap_storage,
        manage_icaap_attachments.attachments,
        "upload_attachment",
        3,
        "file",
        {"kind": "evidence", "title": "Synthetic evidence"},
    ),
    UploadCase(
        manage_icaap_supervisory_addons.router,
        f"/banks/{_BANK}/icaap/supervisory-addons",
        manage_icaap_supervisory_addons.get_icaap_storage,
        manage_icaap_supervisory_addons.supervisory_addons,
        "create_addon",
        2,
        "letter",
        {
            "letter_reference": "synthetic",
            "letter_date": "2026-01-01",
            "effective_from": "2026-01-01",
            "applies_to_basis": "solo",
            "basis": "fixed",
            "basis_value": "1",
        },
    ),
    UploadCase(
        manage_package_attachments.router,
        f"/banks/{_BANK}/regulatory-packages/{_OBJECT}/attachments",
        manage_icaap_attachments.get_icaap_storage,
        manage_package_attachments.package_attachments,
        "upload_package_attachment",
        4,
        "file",
        {"kind": "board_resolution", "title": "Synthetic resolution"},
    ),
]
upload_cases = pytest.mark.parametrize(
    "upload_case", _CASES, ids=["icaap", "supervisory-addon", "reporting"]
)


@pytest.fixture(autouse=True)
def isolated_factory() -> Iterator[None]:
    factory.get_storage_client.cache_clear()
    try:
        yield
    finally:
        factory.get_storage_client.cache_clear()


@pytest.fixture
def upload_app(upload_case: UploadCase) -> Iterator[FastAPI]:
    app = FastAPI()
    app.include_router(upload_case.router)
    ctx = TenantContext(organization_id="OR-SAMP0001")
    bank = Bank(id=_BANK, organization_id=ctx.organization_id)
    icaap_access = deps.IcaapAccess(ctx=ctx, bank=bank)
    package_access = deps.PackageAccess(ctx=ctx, bank=bank, package=RegulatoryPackage())
    with Session() as session:
        app.dependency_overrides[deps.get_tenant_db_session] = lambda: session
        app.dependency_overrides[deps.require_icaap_edit] = lambda: icaap_access
        app.dependency_overrides[deps.require_icaap_create] = lambda: icaap_access
        app.dependency_overrides[deps.require_package_edit] = lambda: package_access
        yield app


@upload_cases
@pytest.mark.parametrize(
    "error",
    [
        StorageValidationError("unconfigured"),
        StorageRetiredError("retired"),
        NotImplementedError("unsupported backend"),
    ],
    ids=["unconfigured", "retired", "unsupported"],
)
def test_storage_initialization_failure_is_503_on_each_route(
    upload_case: UploadCase,
    upload_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def unavailable() -> Never:
        raise error

    monkeypatch.setattr(factory, "get_storage_settings", unavailable)
    response = TestClient(upload_app).post(
        upload_case.path,
        data=upload_case.data,
        files={upload_case.file_field: ("synthetic.pdf", b"%PDF-synthetic", "application/pdf")},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": f"Storage is unavailable: {error}"}
    with pytest.raises(HTTPException) as raised:
        upload_case.dependency()
    assert raised.value.__cause__ is error


def test_storage_factory_returns_its_existing_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemoryStorageClient()
    constructed: list[StorageClient] = []
    settings = StorageEngineSettings.model_construct(backend="s3", env="dev")

    def construct(*_args: object, **_kwargs: object) -> StorageClient:
        constructed.append(storage)
        return storage

    monkeypatch.setattr(factory, "get_storage_settings", lambda: settings)
    monkeypatch.setattr(factory, "S3CompatibleStorageClient", construct)
    for case in _CASES:
        assert case.dependency is get_http_storage
        assert case.dependency() is storage
        assert case.dependency() is factory.get_storage_client()
    assert constructed == [storage]


@pytest.mark.parametrize(
    ("settings", "cause", "message"),
    [
        (
            StorageEngineSettings.model_construct(),
            StorageValidationError,
            "Storage endpoint and credentials are not configured.",
        ),
        (
            StorageEngineSettings.model_construct(
                backend="s3", env="mvp", retire_after=date(2000, 1, 1)
            ),
            StorageRetiredError,
            "MVP storage passed its retirement date",
        ),
        (
            StorageEngineSettings.model_construct(backend="gcs"),
            NotImplementedError,
            "Storage backend 'gcs' is not implemented yet",
        ),
    ],
    ids=["unconfigured", "retired", "unsupported"],
)
def test_real_factory_configuration_refuses_before_network_access(
    monkeypatch: pytest.MonkeyPatch,
    settings: StorageEngineSettings,
    cause: type[Exception],
    message: str,
) -> None:
    monkeypatch.setattr(factory, "get_storage_settings", lambda: settings)
    with pytest.raises(HTTPException) as raised:
        get_http_storage()
    assert raised.value.status_code == 503
    assert isinstance(raised.value.__cause__, cause)
    assert message in str(raised.value.detail)


@upload_cases
def test_unexpected_factory_errors_are_not_relabelled(
    upload_case: UploadCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = RuntimeError("unexpected construction defect")

    def unexpected() -> Never:
        raise error

    monkeypatch.setattr(factory, "get_storage_settings", unexpected)
    with pytest.raises(RuntimeError) as raised:
        upload_case.dependency()
    assert raised.value is error


@upload_cases
def test_existing_override_key_passes_the_exact_storage_to_each_service(
    upload_case: UploadCase,
    upload_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = InMemoryStorageClient()
    received: list[object] = []

    def unexpected() -> Never:
        pytest.fail("an overridden storage dependency must not construct storage")

    def receive(*args: object, **_kwargs: object) -> Never:
        received.append(args[upload_case.storage_argument])
        raise HTTPException(status_code=418, detail="storage reached the service")

    monkeypatch.setattr(factory, "get_storage_settings", unexpected)
    monkeypatch.setattr(upload_case.service, upload_case.operation, receive)
    upload_app.dependency_overrides[upload_case.dependency] = lambda: storage
    response = TestClient(upload_app).post(
        upload_case.path,
        data=upload_case.data,
        files={upload_case.file_field: ("synthetic.pdf", b"%PDF-synthetic", "application/pdf")},
    )
    assert response.status_code == 418
    assert response.json() == {"detail": "storage reached the service"}
    assert len(received) == 1
    assert received[0] is storage
