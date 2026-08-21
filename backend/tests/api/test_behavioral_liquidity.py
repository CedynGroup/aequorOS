from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from tests.api.helpers import ORG_1, headers
from tests.factories.canonical import seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def test_behavioral_liquidity_route_precedes_model_wildcard(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/behavioral/liquidity",
        headers=headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asOfDate"]
    assert body["segments"]
    assert {row["dimension"] for row in body["segments"]} <= {
        "product",
        "customer_segment",
        "concentration_group",
        "branch",
    }