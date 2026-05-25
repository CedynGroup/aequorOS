from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_engine
from app.main import create_app


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_NAME", "risk-service")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)
