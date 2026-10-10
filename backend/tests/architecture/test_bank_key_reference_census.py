from __future__ import annotations

from fastapi.routing import APIRoute

from app.operator.main import create_operator_app
from tests.operator.test_bank_encryption import KEY_REFERENCE_CENSUS


def test_bank_key_reference_census_matches_the_mounted_staff_routes() -> None:
    live = {
        (method, route.path)
        for route in create_operator_app().routes
        if isinstance(route, APIRoute) and "encryption-key" in route.path
        for method in route.methods
    }
    assert live == set(KEY_REFERENCE_CENSUS)
