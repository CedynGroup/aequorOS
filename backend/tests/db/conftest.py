import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def db_client(isolated_db_client: TestClient) -> TestClient:
    """Schema and migration tests require a fresh, independently committing database."""
    return isolated_db_client


@pytest.fixture
def db_session(isolated_db_session: Session) -> Session:
    """Schema and migration tests require a fresh, independently committing database."""
    return isolated_db_session
