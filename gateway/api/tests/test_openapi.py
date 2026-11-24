# pyright: standard
"""The committed OpenAPI exports must match what the service serves."""

from __future__ import annotations

import json

from app.openapi_export import CONTRACT_DIR, exported


def test_committed_exports_are_fresh() -> None:
    for name, text in exported().items():
        path = CONTRACT_DIR / name
        assert path.exists(), f"{name} missing: run scripts/export_openapi.py"
        assert path.read_text() == text, f"{name} is stale: run scripts/export_openapi.py"


def test_exports_cover_the_contract_paths() -> None:
    documents = {name: json.loads(text) for name, text in exported().items()}
    institution = documents["institution-api.v1.openapi.json"]["paths"]
    assert set(institution) == {
        "/oauth/token",
        "/v1/.well-known/jwks.json",
        "/v1/returns",
        "/v1/returns/{code}",
        "/v1/returns/{code}/versions/{version}/schema",
        "/v1/obligations",
        "/v1/submissions",
        "/v1/submissions/{receipt_id}",
        "/v1/submissions/{receipt_id}/amendments",
        "/v1/submissions/{receipt_id}/receipt",
        "/v1/queries",
        "/v1/queries/{number}/acknowledgements",
        "/v1/queries/{number}/responses",
    }
    portal = documents["portal-api.openapi.json"]["paths"]
    assert all(path.startswith("/portal/api/") for path in portal)
    assert "/portal/api/onboarding" in portal
    assert not any("/dev/" in path for doc in documents.values() for path in doc["paths"])
    assert "/ops/api/institutions/{code}/clients" in documents["operator-api.openapi.json"]["paths"]
