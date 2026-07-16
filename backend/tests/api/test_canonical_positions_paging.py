"""Server pagination, filters, and facets for the canonical-positions blotter.

Seeds the deterministic canonical fixture (20 current-generation positions:
9 loans, 6 deposits, 2 securities, 2 interbank, 1 LC) and exercises the paged
listCanonicalPositions contract plus the listCanonicalPositionFacets rollup.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

# The fixture's current-generation identity book. LOAN/OLD's *snapshot* is
# superseded (its identity is not), so it lists without snapshot fields and
# drops out under an as_of_date filter.
TOTAL_POSITIONS = 20
LOAN_COUNT = 9
DEPOSIT_COUNT = 6
USD_COUNT = 3

POSITIONS_URL = f"/api/v1/banks/{SAMPLE_BANK_ID}/canonical-positions"
FACETS_URL = f"{POSITIONS_URL}/facets"


def _seed(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()


def _get(db_client: TestClient, **params: Any) -> dict[str, Any]:
    response = db_client.get(POSITIONS_URL, headers=headers(), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_default_page_returns_first_hundred_with_total(db_client: TestClient) -> None:
    _seed(db_client)
    body = _get(db_client)
    assert body["total"] == TOTAL_POSITIONS
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert len(body["positions"]) == TOTAL_POSITIONS
    references = [position["source_reference"] for position in body["positions"]]
    assert references == sorted(references)


def test_pages_are_disjoint_and_ordering_is_stable(db_client: TestClient) -> None:
    _seed(db_client)
    first = _get(db_client, limit=7, offset=0)
    second = _get(db_client, limit=7, offset=7)
    third = _get(db_client, limit=7, offset=14)
    assert first["total"] == second["total"] == third["total"] == TOTAL_POSITIONS
    assert [len(page["positions"]) for page in (first, second, third)] == [7, 7, 6]

    ids = [p["id"] for page in (first, second, third) for p in page["positions"]]
    assert len(set(ids)) == TOTAL_POSITIONS  # disjoint windows cover the book

    # Re-reading a window yields the identical slice (deterministic ordering).
    assert _get(db_client, limit=7, offset=7)["positions"] == second["positions"]
    # Windows concatenate in the same order as one big read.
    full = _get(db_client, limit=500, offset=0)
    assert [p["id"] for p in full["positions"]] == ids


def test_filters_compose_and_count_the_filtered_set(db_client: TestClient) -> None:
    _seed(db_client)
    loans = _get(db_client, position_type="LOAN")
    assert loans["total"] == LOAN_COUNT
    assert all(p["position_type"] == "LOAN" for p in loans["positions"])

    # Currency is uppercase-normalized server-side.
    usd = _get(db_client, currency="usd")
    assert usd["total"] == USD_COUNT
    assert all(p["currency"] == "USD" for p in usd["positions"])

    dep = _get(db_client, q="dep/")
    assert dep["total"] == DEPOSIT_COUNT
    assert all("DEP/" in p["source_reference"] for p in dep["positions"])

    combined = _get(db_client, position_type="LOAN", currency="USD", q="usd")
    assert combined["total"] == 1
    assert combined["positions"][0]["source_reference"] == "LOAN/USD"

    none = _get(db_client, q="no-such-reference")
    assert none["total"] == 0
    assert none["positions"] == []


def test_as_of_date_keeps_snapshot_gating_semantics(db_client: TestClient) -> None:
    _seed(db_client)
    dated = _get(db_client, as_of_date=FIXTURE_AS_OF.isoformat())
    # LOAN/OLD has no current snapshot at the fixture date, so it drops out.
    assert dated["total"] == TOTAL_POSITIONS - 1
    references = {p["source_reference"] for p in dated["positions"]}
    assert "LOAN/OLD" not in references

    other_day = _get(db_client, as_of_date="2001-01-01")
    assert other_day["total"] == 0


def test_limit_validation_rejects_out_of_range_values(db_client: TestClient) -> None:
    _seed(db_client)
    for bad_limit in (0, 501):
        response = db_client.get(
            POSITIONS_URL, headers=headers(), params={"limit": bad_limit}
        )
        assert response.status_code == 422, response.text


def test_facets_report_types_currencies_and_total(db_client: TestClient) -> None:
    _seed(db_client)
    response = db_client.get(FACETS_URL, headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == TOTAL_POSITIONS

    types = {facet["value"]: facet["count"] for facet in body["position_types"]}
    assert types == {
        "LOAN": LOAN_COUNT,
        "DEPOSIT": DEPOSIT_COUNT,
        "SECURITY_HOLDING": 2,
        "INTERBANK_PLACEMENT": 1,
        "INTERBANK_BORROWING": 1,
        "LC_GUARANTEE": 1,
    }
    # Ordered by count descending, then value, so dropdowns render stably.
    counts = [facet["count"] for facet in body["position_types"]]
    assert counts == sorted(counts, reverse=True)

    currencies = {facet["value"]: facet["count"] for facet in body["currencies"]}
    assert currencies == {"GHS": TOTAL_POSITIONS - USD_COUNT, "USD": USD_COUNT}


def test_positions_and_facets_are_tenant_scoped(db_client: TestClient) -> None:
    _seed(db_client)
    for url in (POSITIONS_URL, FACETS_URL):
        response = db_client.get(url, headers=headers(org_id=ORG_2))
        assert response.status_code == 404, response.text
