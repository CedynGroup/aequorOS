import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Schema and migration writes must commit against a fresh database."""
    for item in items:
        fixture_names = getattr(item, "fixturenames", ())
        if item.nodeid.startswith("tests/db/") and {
            "db_client",
            "db_session",
        }.intersection(fixture_names):
            item.add_marker(pytest.mark.committing_db)
