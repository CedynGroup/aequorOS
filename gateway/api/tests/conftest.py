# pyright: standard
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.harness import Harness, make_harness, migrate, pg_url, truncate_all


@pytest.fixture(scope="session")
def keys_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("keys")


@pytest.fixture(scope="session")
def sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    migrate(f"sqlite+pysqlite:///{path}")
    return path


@pytest.fixture
def harness(tmp_path: Path, keys_dir: Path, sqlite_template: Path) -> Iterator[Harness]:
    h = make_harness(tmp_path, keys_dir, sqlite_template)
    yield h
    h.client.close()
    h.engine.dispose()


@pytest.fixture(scope="session")
def pg_schema() -> Iterator[tuple[str, str]]:
    url = pg_url()
    if url is None:
        pytest.skip("GATEWAY_TEST_DATABASE_URL is not set")
    schema = f"srg_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        migrate(url, schema=schema)
        yield url, schema
    finally:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.fixture
def pg_harness(tmp_path: Path, keys_dir: Path, pg_schema: tuple[str, str]) -> Iterator[Harness]:
    url, schema = pg_schema
    h = make_harness(tmp_path, keys_dir, None, database_url=url, schema=schema)
    truncate_all(h.engine)
    yield h
    h.client.close()
    h.engine.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def any_harness(request: pytest.FixtureRequest) -> Harness:
    if request.param == "postgres":
        return request.getfixturevalue("pg_harness")
    return request.getfixturevalue("harness")
