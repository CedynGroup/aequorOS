from __future__ import annotations

from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker
from app.features.ingest_data import get_ingestion_storage
from app.integrations.storage.s3 import get_object_storage
from app.models import Organization, RiskCase
from tests.api.factories import CaseFactory
from tests.storage.inmemory import InMemoryStorageClient

_ROLLBACK_ORGANIZATION_ID = "OR-TXROLL01"
_REUSED_STATE: dict[str, int] = {}


def test_committed_write_is_visible_inside_rollback_fixture(db_session: Session) -> None:
    db_session.add(
        Organization(
            id=_ROLLBACK_ORGANIZATION_ID,
            name="Rollback Isolation Contract",
        )
    )
    db_session.commit()

    assert db_session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is not None


def test_committed_write_is_absent_after_rollback_fixture(db_session: Session) -> None:
    assert db_session.get(Organization, _ROLLBACK_ORGANIZATION_ID) is None


def test_api_write_is_visible_to_direct_bound_session(db_client: TestClient) -> None:
    case = CaseFactory(db_client).create()

    with get_sessionmaker()() as session:
        assert session.get(RiskCase, case.id) is not None


def test_shared_app_keeps_client_and_storage_state_function_scoped(
    db_client: TestClient,
    fake_storage: object,
    storage_engine: InMemoryStorageClient,
) -> None:
    app = cast("FastAPI", db_client.app)
    _REUSED_STATE["app"] = id(app)
    object_storage = app.dependency_overrides[get_object_storage]()
    ingestion_storage = app.dependency_overrides[get_ingestion_storage]()
    assert object_storage is fake_storage
    assert ingestion_storage is storage_engine
    _REUSED_STATE["object_storage"] = id(object_storage)
    _REUSED_STATE["ingestion_storage"] = id(ingestion_storage)

    db_client.cookies.set("fixture-state", "must-not-leak")
    db_client.headers["x-fixture-state"] = "must-not-leak"


def test_reused_app_receives_fresh_client_and_storage_state(
    db_client: TestClient,
    fake_storage: object,
    storage_engine: InMemoryStorageClient,
) -> None:
    app = cast("FastAPI", db_client.app)
    assert id(app) == _REUSED_STATE["app"]
    assert db_client.cookies.get("fixture-state") is None
    assert "x-fixture-state" not in db_client.headers

    object_storage = app.dependency_overrides[get_object_storage]()
    ingestion_storage = app.dependency_overrides[get_ingestion_storage]()
    assert object_storage is fake_storage
    assert ingestion_storage is storage_engine
    assert id(object_storage) != _REUSED_STATE["object_storage"]
    assert id(ingestion_storage) != _REUSED_STATE["ingestion_storage"]


def test_isolated_session_exposes_a_real_engine(
    isolated_db_session: Session,
) -> None:
    assert isinstance(isolated_db_session.get_bind(), Engine)
