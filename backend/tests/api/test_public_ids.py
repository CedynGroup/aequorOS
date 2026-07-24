"""Platform IDs ARE the institution identity.

Banks and organizations are keyed by short platform-generated identifiers
(BK-XXXXXXXX / OR-XXXXXXXX, Crockford base32) — the primary key, the API
path token, and the ID banks integrate with. No UUID aliases. The hermetic
fixture pins deterministic codes; real tenants get generator-assigned codes
from the model defaults at creation.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.services.public_ids import (
    generate_public_id,
    is_public_id,
    new_bank_public_id,
    new_organization_public_id,
)
from app.services.sample_bank_seed import DEMO_ORG_ID, SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers


def _seed(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text


def _get_bank(db_client: TestClient, reference: str) -> dict[str, Any]:
    response = db_client.get(f"/api/v1/banks/{reference}", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_generator_format_and_charset() -> None:
    for _ in range(200):
        bank_code = new_bank_public_id()
        org_code = new_organization_public_id()
        assert is_public_id(bank_code)
        assert is_public_id(org_code)
        assert bank_code.startswith("BK-")
        assert org_code.startswith("OR-")
        # Ambiguous Crockford exclusions never appear.
        assert not set(bank_code[3:]) & set("ILOU")


def test_fixture_ids_are_valid_platform_ids() -> None:
    """The hermetic fixture's pinned IDs obey the same format contract."""
    assert is_public_id(SAMPLE_BANK_ID)
    assert is_public_id(DEMO_ORG_ID)


def test_is_public_id_rejects_non_platform_forms() -> None:
    assert not is_public_id("77000000-0000-4000-8000-000000000001")
    assert not is_public_id("seed-demo")
    assert not is_public_id("BK-SHORT")
    assert not is_public_id("XX-ABCDEFGH")


def test_bank_identity_is_the_platform_id(db_client: TestClient) -> None:
    """One identity: the bank's id IS the platform code — no UUID alias."""
    _seed(db_client)
    bank = _get_bank(db_client, SAMPLE_BANK_ID)
    assert bank["id"] == SAMPLE_BANK_ID
    assert bank["organization_id"] == DEMO_ORG_ID
    assert "public_id" not in bank


def test_bank_paths_tolerate_lowercase_input(db_client: TestClient) -> None:
    _seed(db_client)
    by_lower = _get_bank(db_client, SAMPLE_BANK_ID.lower())
    assert by_lower["id"] == SAMPLE_BANK_ID

    periods = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/reporting-periods", headers=headers()
    )
    assert periods.status_code == 200
    assert periods.json()["bank_id"] == SAMPLE_BANK_ID


def test_platform_id_is_tenant_scoped(db_client: TestClient) -> None:
    """Another organization cannot resolve (or probe) a foreign bank ID."""
    _seed(db_client)
    foreign = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}", headers=headers(org_id=ORG_2)
    )
    assert foreign.status_code == 404


def test_unknown_reference_is_a_clean_404(db_client: TestClient) -> None:
    _seed(db_client)
    for reference in ("BK-ZZZZZZZZ", "not-a-bank-ref"):
        response = db_client.get(f"/api/v1/banks/{reference}", headers=headers())
        assert response.status_code == 404, reference


def test_push_batch_opens_with_platform_id(db_client: TestClient) -> None:
    """The integration surface institutions use takes the ID they were
    onboarded with — the only bank identifier that exists."""
    _seed(db_client)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/push-batches",
        headers=headers(),
        json={
            "as_of_date": "2026-06-30",
            "idempotency_key": "pid-platform-id-1",
            "reason": "platform-ID integration test",
        },
    )
    assert response.status_code == 201, response.text


def test_generate_public_id_uniqueness_sample() -> None:
    codes = {generate_public_id("BK") for _ in range(5000)}
    assert len(codes) == 5000
