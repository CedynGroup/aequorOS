from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.models import Organization, RiskCase
from tests.api.factories import CaseFactory
from tests.conftest import (
    _LazyTestApp,
    _TestDatabase,
    _db_client_lifecycle,
    _rollback_sessionmaker_lifecycle,
)
from tests.storage.inmemory import InMemoryStorageClient

_ROLLBACK_ORGANIZATION_ID = "OR-TXROLL01"


def test_committed_write_is_rolled_back_between_fixture_lifecycles(
    _shared_test_database: _TestDatabase,
) -> None:
    with pytest.MonkeyPatch.context() as first_patch:
        with _rollback_sessionmaker_lifecycle(_shared_test_database, first_patch) as maker:
            with maker() as session:
                session.add(
                    Organization(
                        id=_ROLLBACK_ORGANIZATION_ID,
                        name="Rollback Isolation Contract",
                    )
                )
                session.commit()
                assert session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is not None

    with pytest.MonkeyPatch.context() as second_patch:
        with _rollback_sessionmaker_lifecycle(_shared_test_database, second_patch) as maker:
            with maker() as session:
                assert session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is None


def test_api_write_is_visible_to_direct_bound_session(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    with get_sessionmaker()() as session:
        assert session.get(RiskCase, case.id) is not None


def test_reused_app_receives_fresh_client_and_storage_state(
    _shared_app: _LazyTestApp,
    _shared_test_database: _TestDatabase,
) -> None:
    app = _shared_app.get()
    first_storage = object()
    first_ingestion = InMemoryStorageClient()
    with pytest.MonkeyPatch.context() as first_patch:
        with _rollback_sessionmaker_lifecycle(_shared_test_database, first_patch):
            with _db_client_lifecycle(
                app, first_storage, first_ingestion
            ) as first_client:
                first_client.cookies.set("fixture-state", "must-not-leak")
                first_client.headers["x-fixture-state"] = "must-not-leak"

    second_storage = object()
    second_ingestion = InMemoryStorageClient()
    with pytest.MonkeyPatch.context() as second_patch:
        with _rollback_sessionmaker_lifecycle(_shared_test_database, second_patch):
            with _db_client_lifecycle(
                app, second_storage, second_ingestion
            ) as second_client:
                assert second_client.cookies.get("fixture-state") is None
                assert "x-fixture-state" not in second_client.headers
                assert app.dependency_overrides[get_object_storage]() is second_storage
                assert app.dependency_overrides[get_ingestion_storage]() is second_ingestion


def test_isolated_session_exposes_a_real_engine(
    isolated_db_session: Session,
) -> None:
    assert isinstance(isolated_db_session.get_bind(), Engine)
from app.db.session import get_sessionmaker
