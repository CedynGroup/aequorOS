from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from tests.api.helpers import ORG_1, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def test_universal_bank_gets_shared_liquidity_monitoring(db_client: TestClient) -> None:
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
        f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity-monitoring",
        headers=headers(),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_class"] == "bank"
    assert body["maturity_ladder"]
    assert body["funding_concentration"]["total_deposits_ghs"]
    assert "counterbalancing_capacity" in body
    assert "ratios" not in body
    assert "reserves" not in body