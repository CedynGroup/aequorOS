"""Hermetic fixtures for the operator control-plane suite.

Builds on the root conftest's env guards (blank DATABASE_URL, APP_ENV=test)
and adds the operator-specific ones: dev-token auth ON (that is what is
under test locally), OIDC unset, KMS off — plus a fake S3 client so the
provisioning saga runs the REAL ``provision_institution`` code path with
zero network.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.config import get_operator_settings, get_settings
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker
from app.models import Jurisdiction
from app.operator.features.provision import get_provisioning_clients
from app.operator.main import create_operator_app
from app.operator.services.tenant_provisioning import ProvisioningClients
from app.storage.config import StorageEngineSettings

DEV_TOKEN = "operator-dev-token-for-tests"


def operator_headers(token: str = DEV_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def provision_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "organization_name": "Test Bancorp Holdings",
        "bank_name": "Test Commercial Bank",
        "license_type": "universal_bank",
        "jurisdiction_code": "GH",
        "currency": "GHS",
        "admin_email": "admin@testbank.example",
        "admin_full_name": "Ama Mensah",
    }
    payload.update(overrides)
    return payload


def fake_storage_settings() -> StorageEngineSettings:
    """Explicit settings, .env loading disabled — a developer's local S3
    values can never leak into the fake-client tests."""
    return StorageEngineSettings(
        _env_file=None,  # type: ignore[call-arg] - pydantic-settings init kwarg
        STORAGE_BACKEND="minio",
        STORAGE_ENV="dev",
        S3_ENDPOINT="http://fake-s3.test:9000",
        S3_REGION="us-east-1",
        S3_ACCESS_KEY="fake-access",
        S3_SECRET_KEY="fake-secret",
    )


class FakeS3Client:
    """The handful of S3 calls ``provision_institution`` + the saga make.

    ``fail_on`` injects a deterministic failure at one operation so rollback
    behavior is testable without a flaky backend.
    """

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.buckets: dict[str, dict[str, bytes]] = {}
        self.versioned: set[str] = set()
        self.lifecycles: dict[str, Any] = {}
        self.encryption: dict[str, Any] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def _record(self, operation: str, bucket: str) -> None:
        self.calls.append((operation, bucket))
        if self.fail_on == operation:
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": f"injected {operation} failure"}},
                operation,
            )

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803 - boto3 API shape
        self._record("head_bucket", Bucket)
        if Bucket not in self.buckets:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket")

    def create_bucket(self, *, Bucket: str, **kwargs: Any) -> None:  # noqa: N803
        self._record("create_bucket", Bucket)
        self.buckets[Bucket] = {}
        self.lifecycles.setdefault("__create_kwargs__", {})[Bucket] = kwargs

    def put_bucket_versioning(self, *, Bucket: str, **_: Any) -> None:  # noqa: N803
        self._record("put_bucket_versioning", Bucket)
        self.versioned.add(Bucket)

    def put_bucket_lifecycle_configuration(  # noqa: N803
        self, *, Bucket: str, LifecycleConfiguration: Any
    ) -> None:
        self._record("put_bucket_lifecycle_configuration", Bucket)
        self.lifecycles[Bucket] = LifecycleConfiguration

    def put_bucket_encryption(  # noqa: N803
        self, *, Bucket: str, ServerSideEncryptionConfiguration: Any
    ) -> None:
        self._record("put_bucket_encryption", Bucket)
        self.encryption[Bucket] = ServerSideEncryptionConfiguration

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self._record("put_object", Bucket)
        self.buckets[Bucket][Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self._record("get_object", Bucket)
        return {"Body": io.BytesIO(self.buckets[Bucket][Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self._record("delete_object", Bucket)
        self.buckets[Bucket].pop(Key, None)

    def delete_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        self._record("delete_bucket", Bucket)
        if self.buckets.get(Bucket):
            raise ClientError(
                {"Error": {"Code": "BucketNotEmpty", "Message": "not empty"}}, "DeleteBucket"
            )
        self.buckets.pop(Bucket, None)


class FakeKmsClient:
    """The three KMS calls the saga makes (create/alias/schedule-deletion)."""

    KEY_ID = "11111111-2222-3333-4444-555555555555"
    ARN = f"arn:aws:kms:us-east-1:000000000000:key/{KEY_ID}"

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.scheduled_deletions: list[str] = []

    def create_key(self, **_: Any) -> dict[str, Any]:
        return {"KeyMetadata": {"KeyId": self.KEY_ID, "Arn": self.ARN}}

    def create_alias(self, *, AliasName: str, TargetKeyId: str) -> None:  # noqa: N803
        self.aliases[AliasName] = TargetKeyId

    def schedule_key_deletion(  # noqa: N803
        self, *, KeyId: str, PendingWindowInDays: int
    ) -> None:
        _ = PendingWindowInDays
        self.scheduled_deletions.append(KeyId)


@pytest.fixture(autouse=True)
def operator_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin every OPERATOR_* setting so a developer's .env cannot leak in
    (same discipline as the root conftest's guards)."""
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "1")
    monkeypatch.setenv("OPERATOR_DEV_TOKEN", DEV_TOKEN)
    monkeypatch.setenv("OPERATOR_DEV_EMAIL", "dev@aequoros.com")
    monkeypatch.setenv("OPERATOR_CORS_ORIGINS", "")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "")
    monkeypatch.setenv("OPERATOR_OIDC_ISSUER", "")
    monkeypatch.setenv("OPERATOR_OIDC_CLIENT_ID", "")
    monkeypatch.setenv("OPERATOR_OIDC_ALLOWED_DOMAIN", "aequoros.com")
    monkeypatch.setenv("OPERATOR_AWS_KMS_ENABLED", "0")
    monkeypatch.setenv("OPERATOR_PORT", "8100")
    get_operator_settings.cache_clear()
    yield
    get_operator_settings.cache_clear()


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _seed_jurisdictions(engine: Engine) -> None:
    with Session(engine) as session:
        session.add_all(
            [
                Jurisdiction(
                    code="GH",
                    country_name="Ghana",
                    currency_code="GHS",
                    currency_name="Ghana Cedi",
                    locale="en-GH",
                    central_bank_name="Bank of Ghana",
                    regulator_short="BoG",
                    submission_portal="ORASS",
                    timezone="Africa/Accra",
                ),
                Jurisdiction(
                    code="NG",
                    country_name="Nigeria",
                    currency_code="NGN",
                    currency_name="Nigerian Naira",
                    locale="en-NG",
                    central_bank_name="Central Bank of Nigeria",
                    regulator_short="CBN",
                    submission_portal=None,
                    timezone="Africa/Lagos",
                ),
            ]
        )
        session.commit()


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def operator_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_s3: FakeS3Client,
) -> Iterator[TestClient]:
    """Operator app over a fresh SQLite database with the fake S3 injected."""
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operator_test.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    get_engine.cache_clear()

    engine = get_engine(database_url)
    _enable_sqlite_foreign_keys(engine)
    try:
        Base.metadata.create_all(engine)
        _seed_jurisdictions(engine)
        app = create_operator_app()
        app.dependency_overrides[get_provisioning_clients] = lambda: ProvisioningClients(
            s3_client=fake_s3,
            storage_settings=fake_storage_settings(),
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
def operator_db(operator_client: TestClient) -> Iterator[Session]:
    """A session on the same SQLite database the operator client uses."""
    _ = operator_client
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
