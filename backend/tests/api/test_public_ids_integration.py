"""Platform IDs ARE the institution identity — on the ACTUAL primary.

Banks and organizations are keyed by short platform-generated identifiers
(BK-XXXXXXXX / OR-XXXXXXXX, Crockford base32) — the primary key, the API path
token, and the ID banks integrate with. No UUID aliases. Invariants: generator
format/charset/uniqueness (pure), the real tenant's ids obey the contract, the
bank's id IS its platform code (no alias field), lowercase input resolves, a
foreign tenant cannot resolve or probe it (404), unknown references 404 cleanly,
and the push surface opens on the platform id. DB-backed tests are opt-in via
REAL_DATA_DATABASE_URL and rolled back (tests/real_data.py).

This module's other-suite cases live in ``test_public_ids.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Bank, Organization
from app.services.public_ids import (
    is_public_id,
)
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    REAL_OTHER_BANK_ID,
    other_headers,
    real_headers,
    requires_real_data,
)


def _get_bank(client: TestClient, reference: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/banks/{reference}", headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()


@requires_real_data
def test_every_stored_bank_and_org_id_is_a_platform_id(real_session: Session) -> None:
    """Epoch 2026-07-24: no UUID keys survive for banks/orgs — every row the
    tenant can see carries a generator-shaped code, and the bank's org id
    resolves to a real organization."""
    real_session.info["organization_id"] = REAL_ORG_ID
    org_ids = list(real_session.scalars(select(Organization.id)))
    bank_rows = list(real_session.execute(select(Bank.id, Bank.organization_id)))
    assert REAL_ORG_ID in org_ids
    assert REAL_BANK_ID in {bank_id for bank_id, _ in bank_rows}
    for org_id in org_ids:
        assert is_public_id(org_id) and org_id.startswith("OR-"), org_id
    for bank_id, org_id in bank_rows:
        assert is_public_id(bank_id) and bank_id.startswith("BK-"), bank_id
        assert org_id in org_ids


@requires_real_data
def test_bank_identity_is_the_platform_id(real_client: TestClient) -> None:
    """One identity: the bank's id IS the platform code — no UUID alias."""
    bank = _get_bank(real_client, REAL_BANK_ID)
    assert bank["id"] == REAL_BANK_ID
    assert bank["organization_id"] == REAL_ORG_ID
    assert "public_id" not in bank
    listed = real_client.get("/api/v1/banks", headers=real_headers()).json()["banks"]
    assert REAL_BANK_ID in {item["id"] for item in listed}
    assert all(is_public_id(item["id"]) for item in listed)


@requires_real_data
def test_bank_paths_tolerate_lowercase_input(real_client: TestClient) -> None:
    by_lower = _get_bank(real_client, REAL_BANK_ID.lower())
    assert by_lower["id"] == REAL_BANK_ID

    periods = real_client.get(
        f"/api/v1/banks/{REAL_BANK_ID.lower()}/reporting-periods", headers=real_headers()
    )
    assert periods.status_code == 200
    assert periods.json()["bank_id"] == REAL_BANK_ID


@requires_real_data
def test_platform_id_is_tenant_scoped(real_client: TestClient) -> None:
    """Another organization cannot resolve (or probe) a foreign bank ID — in
    either direction."""
    foreign = real_client.get(f"/api/v1/banks/{REAL_BANK_ID}", headers=other_headers())
    assert foreign.status_code == 404
    reverse = real_client.get(f"/api/v1/banks/{REAL_OTHER_BANK_ID}", headers=real_headers())
    assert reverse.status_code == 404
    # ...while each tenant resolves its own.
    assert (
        real_client.get(f"/api/v1/banks/{REAL_OTHER_BANK_ID}", headers=other_headers()).json()["id"]
        == REAL_OTHER_BANK_ID
    )


@requires_real_data
def test_unknown_reference_is_a_clean_404(real_client: TestClient) -> None:
    for reference in ("BK-ZZZZZZZZ", "not-a-bank-ref", str(uuid4())):
        response = real_client.get(f"/api/v1/banks/{reference}", headers=real_headers())
        assert response.status_code == 404, reference


@requires_real_data
def test_push_batch_opens_with_platform_id(real_client: TestClient) -> None:
    """The integration surface institutions use takes the ID they were
    onboarded with — the only bank identifier that exists."""
    response = real_client.post(
        f"/api/v1/banks/{REAL_BANK_ID}/push-batches",
        headers=real_headers(),
        json={
            "as_of_date": "2026-06-30",
            "idempotency_key": f"pid-platform-id-{uuid4().hex}",
            "reason": "platform-ID integration test",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bank_id"] == REAL_BANK_ID
