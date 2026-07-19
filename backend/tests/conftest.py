from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.base import PresignedUpload, StoredObjectHead
from app.integrations.storage.s3 import get_object_storage
from app.main import create_app
from app.models import Organization, User
from tests.api.factories import ApiFactories
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2
from tests.storage.inmemory import InMemoryStorageClient


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_NAME", "risk-service")
    # Set (not delete) so a developer's local .env cannot leak into the suite:
    # environment variables take priority over the env_file in pydantic-settings,
    # and the settings treat "" as unconfigured. Deleting the variable is NOT
    # enough — pydantic-settings would still read the value from .env (the
    # remote aequoros database, which no test may ever touch implicitly;
    # Postgres-gated tests opt in explicitly via TEST_DATABASE_URL and run in
    # a disposable per-run schema).
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("WORKER_DATABASE_URL", "")
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    # Same .env-leak guard: a developer's local CREDENTIAL_VAULT_MASTER_KEY must
    # not leak in, or tests asserting the "vault unconfigured" path (credential
    # material refused) would wrongly see a configured vault. "" reads as
    # unconfigured; tests that need a key set it themselves via monkeypatch. CI
    # has no .env, so the key is already absent there.
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "")
    # Same guard for the in-process worker: a developer's RUN_INPROCESS_WORKER=1
    # would make every TestClient startup spawn a real poll thread against the
    # blanked database — hundreds of "Worker poll iteration failed" logs and
    # cross-test flakiness. Tests exercise worker handlers directly instead.
    monkeypatch.setenv("RUN_INPROCESS_WORKER", "0")
    # A signing secret so the auth layer is exercised in tests (prod sets its own).
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-signing-secret-not-for-production-00")
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture(autouse=True)
def hermetic_etl_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off developer-local trained ML-ETL artifacts.

    Ingestion / dedup load a bank's per-tenant RandomForest / IsolationForest from
    the untracked ``artifacts/etl_models/{org}/{bank}`` tree when present, so a
    batch's dedup / anomaly output would otherwise depend on whether a developer had
    trained that bank's models locally (absent in CI). Force the deterministic path
    here; a test that exercises a trained model injects it explicitly via
    ``EtlConfig`` (see ``tests/etl/test_pipeline.py``).
    """
    monkeypatch.setattr("app.etl.model_loading.load_counterparty_model", lambda *a, **k: None)
    monkeypatch.setattr("app.etl.model_loading.load_anomaly_model", lambda *a, **k: None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


class FakeStorage:
    head: StoredObjectHead | None = None
    download_url = "https://storage.local/download"
    deleted: list[tuple[str, str]] = []

    def create_presigned_upload_url(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> PresignedUpload:
        return PresignedUpload(
            url=f"https://storage.local/{bucket}/{object_key}",
            method="PUT",
            headers={"Content-Type": content_type},
            expires_in_seconds=expires_seconds,
        )

    def create_presigned_download_url(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_seconds: int,
    ) -> str:
        return self.download_url

    def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None:
        return self.head

    def delete_object(self, *, bucket: str, object_key: str) -> None:
        self.deleted.append((bucket, object_key))


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _postgres_schema_url(database_url: str, schema_name: str) -> str:
    url = make_url(database_url)
    return url.update_query_dict({"options": f"-csearch_path={schema_name}"}).render_as_string(
        hide_password=False
    )


def _prepare_database_url(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str | None]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if test_database_url is None:
        database_path = tmp_path / "risk_service_test.db"
        return f"sqlite+pysqlite:///{database_path}", None

    schema_name = f"risk_service_test_{uuid4().hex}"
    admin_engine = create_engine(test_database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    finally:
        admin_engine.dispose()

    monkeypatch.setenv("DATABASE_URL", _postgres_schema_url(test_database_url, schema_name))
    return os.environ["DATABASE_URL"], schema_name


def _drop_postgres_schema(database_url: str, schema_name: str | None) -> None:
    if schema_name is None:
        return

    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
    finally:
        admin_engine.dispose()


def _seed_demo_tenants(engine: Engine) -> None:
    with Session(engine) as session:
        session.add_all(
            [
                Organization(id=ORG_1, name="Demo Tenant 1"),
                Organization(id=ORG_2, name="Demo Tenant 2"),
                User(
                    id=USER_1,
                    organization_id=ORG_1,
                    email="demo.user.one@example.test",
                    display_name="Demo User One",
                ),
                User(
                    id=USER_2,
                    organization_id=ORG_2,
                    email="demo.user.two@example.test",
                    display_name="Demo User Two",
                ),
            ]
        )
        session.commit()


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def storage_engine() -> InMemoryStorageClient:
    return InMemoryStorageClient()


@pytest.fixture
def api_factories(db_client: TestClient, fake_storage: FakeStorage) -> ApiFactories:
    return ApiFactories(db_client, fake_storage)


@pytest.fixture
def test_settings() -> Settings:
    return get_settings()


@pytest.fixture
def db_settings(db_client: TestClient) -> Settings:
    _ = db_client
    return get_settings()


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


@pytest.fixture
def db_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_storage: FakeStorage,
    storage_engine: InMemoryStorageClient,
) -> Iterator[TestClient]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    database_url, schema_name = _prepare_database_url(tmp_path=tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    get_engine.cache_clear()

    engine = get_engine(database_url)
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    try:
        Base.metadata.create_all(engine)
        _seed_demo_tenants(engine)
        app = create_app()
        app.dependency_overrides[get_object_storage] = lambda: fake_storage
        app.dependency_overrides[get_ingestion_storage] = lambda: storage_engine
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        if test_database_url is not None:
            _drop_postgres_schema(test_database_url, schema_name)


@pytest.fixture
def db_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    database_url, schema_name = _prepare_database_url(tmp_path=tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    get_engine.cache_clear()

    engine = get_engine(database_url)
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    session: Session | None = None
    try:
        Base.metadata.create_all(engine)
        _seed_demo_tenants(engine)
        session = get_sessionmaker()()
        yield session
    finally:
        if session is not None:
            session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        if test_database_url is not None:
            _drop_postgres_schema(test_database_url, schema_name)
