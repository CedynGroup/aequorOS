"""Server pagination, filters, and facets for the canonical-positions blotter on
the ACTUAL primary (the real Sample Bank book: ~570k current positions).

Every assertion is a paging INVARIANT over small windows — never a magnitude,
never a full read: totals are stable across pages, windows are disjoint and
concatenate in the same order as one wider read (deterministic ordering by
source_reference, then id), filters compose and count the filtered set,
as_of_date keeps the snapshot-gating semantics, facets reconcile to the listing
totals and each other, out-of-range limits 422, and a foreign tenant sees 404.
Opt-in via REAL_DATA_DATABASE_URL, rolled back (tests/real_data.py).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.real_data import REAL_BANK_ID, other_headers, real_headers, requires_real_data

pytestmark = requires_real_data

POSITIONS_URL = f"/api/v1/banks/{REAL_BANK_ID}/canonical-positions"
FACETS_URL = f"{POSITIONS_URL}/facets"
WINDOW = 7


def _get(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(POSITIONS_URL, headers=real_headers(), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _facets(client: TestClient) -> dict[str, Any]:
    response = client.get(FACETS_URL, headers=real_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _sort_key(position: dict[str, Any]) -> tuple[str, str]:
    return (position["source_reference"], position["id"])


def test_default_page_returns_first_hundred_with_total(real_client: TestClient) -> None:
    body = _get(real_client)
    assert body["bank_id"] == REAL_BANK_ID
    assert body["total"] > 0, "the real book must hold current positions"
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert len(body["positions"]) == min(100, body["total"])
    keys = [_sort_key(position) for position in body["positions"]]
    assert keys == sorted(keys)
    for position in body["positions"]:
        assert position["source_reference"]
        assert position["position_type"]
        assert len(position["currency"]) == 3


def test_pages_are_disjoint_and_ordering_is_stable(real_client: TestClient) -> None:
    first = _get(real_client, limit=WINDOW, offset=0)
    second = _get(real_client, limit=WINDOW, offset=WINDOW)
    third = _get(real_client, limit=WINDOW, offset=2 * WINDOW)
    total = first["total"]
    assert total > 3 * WINDOW
    assert second["total"] == third["total"] == total
    assert [len(page["positions"]) for page in (first, second, third)] == [WINDOW] * 3

    ids = [p["id"] for page in (first, second, third) for p in page["positions"]]
    assert len(set(ids)) == 3 * WINDOW  # disjoint windows
    keys = [_sort_key(p) for page in (first, second, third) for p in page["positions"]]
    assert keys == sorted(keys)  # ordering carries across window boundaries

    # Re-reading a window yields the identical slice (deterministic ordering).
    assert _get(real_client, limit=WINDOW, offset=WINDOW)["positions"] == second["positions"]
    # Windows concatenate in the same order as one wider read.
    wide = _get(real_client, limit=3 * WINDOW, offset=0)
    assert [p["id"] for p in wide["positions"]] == ids
    # The tail of the book is reachable: the last window is a partial page and
    # anything past the end is empty, with the same total.
    last_offset = (total // WINDOW) * WINDOW
    tail = _get(real_client, limit=WINDOW, offset=last_offset)
    assert tail["total"] == total
    assert len(tail["positions"]) == total - last_offset
    beyond = _get(real_client, limit=WINDOW, offset=total)
    assert beyond["positions"] == []
    assert beyond["total"] == total


def test_filters_compose_and_count_the_filtered_set(real_client: TestClient) -> None:
    facets = _facets(real_client)
    types = {facet["value"]: facet["count"] for facet in facets["position_types"]}
    currencies = {facet["value"]: facet["count"] for facet in facets["currencies"]}
    # The rarest type/currency keep the filtered walks small.
    rare_type = min(types, key=lambda value: (types[value], value))
    rare_ccy = min(currencies, key=lambda value: (currencies[value], value))

    typed = _get(real_client, position_type=rare_type, limit=WINDOW)
    assert typed["total"] == types[rare_type]
    assert all(p["position_type"] == rare_type for p in typed["positions"])

    # Currency is uppercase-normalized server-side.
    ccy = _get(real_client, currency=rare_ccy.lower(), limit=WINDOW)
    assert ccy["total"] == currencies[rare_ccy]
    assert all(p["currency"] == rare_ccy for p in ccy["positions"])

    # q is a case-insensitive substring match on source_reference: a real
    # reference from the first page must find itself (and only references
    # containing it).
    probe = _get(real_client, limit=1)["positions"][0]["source_reference"]
    needle = probe[: max(3, len(probe) // 2)]
    matched = _get(real_client, q=needle.lower(), limit=WINDOW)
    assert 1 <= matched["total"] <= facets["total"]
    assert all(needle.lower() in p["source_reference"].lower() for p in matched["positions"])

    # Filters compose (AND): the intersection never exceeds either side.
    combined = _get(real_client, position_type=rare_type, currency=rare_ccy, limit=WINDOW)
    assert combined["total"] <= min(typed["total"], ccy["total"])
    assert all(
        p["position_type"] == rare_type and p["currency"] == rare_ccy for p in combined["positions"]
    )

    none = _get(real_client, q="no-such-reference-ever-☃", limit=WINDOW)
    assert none["total"] == 0
    assert none["positions"] == []


def test_as_of_date_keeps_snapshot_gating_semantics(real_client: TestClient) -> None:
    # A position with a snapshot on date D must list under as_of_date=D with
    # exactly that snapshot; the dated total never exceeds the undated one.
    # Composed with the rarest position type so the snapshot-gated walk stays
    # small on a ~570k-position book (the semantics are per-position anyway).
    facets = _facets(real_client)
    types = {facet["value"]: facet["count"] for facet in facets["position_types"]}
    rare_type = min(types, key=lambda value: (types[value], value))

    undated = _get(real_client, position_type=rare_type, limit=1)
    anchor = undated["positions"][0]
    assert anchor["snapshot_id"] is not None
    dated = _get(
        real_client, position_type=rare_type, as_of_date=anchor["as_of_date"], limit=WINDOW
    )
    assert dated["as_of_date"] == anchor["as_of_date"]
    assert 1 <= dated["total"] <= undated["total"]
    assert anchor["id"] in {p["id"] for p in dated["positions"]} or dated["total"] > WINDOW
    assert all(p["as_of_date"] == anchor["as_of_date"] for p in dated["positions"])
    assert all(p["snapshot_id"] is not None for p in dated["positions"])

    other_day = _get(real_client, position_type=rare_type, as_of_date="2001-01-01", limit=WINDOW)
    assert other_day["total"] == 0
    assert other_day["positions"] == []


def test_limit_validation_rejects_out_of_range_values(real_client: TestClient) -> None:
    for bad_limit in (0, 501):
        response = real_client.get(
            POSITIONS_URL, headers=real_headers(), params={"limit": bad_limit}
        )
        assert response.status_code == 422, response.text
    assert (
        real_client.get(POSITIONS_URL, headers=real_headers(), params={"offset": -1}).status_code
        == 422
    )


def test_facets_report_types_currencies_and_total(real_client: TestClient) -> None:
    body = _facets(real_client)
    assert body["bank_id"] == REAL_BANK_ID
    listing_total = _get(real_client, limit=1)["total"]
    assert body["total"] == listing_total > 0

    types = {facet["value"]: facet["count"] for facet in body["position_types"]}
    currencies = {facet["value"]: facet["count"] for facet in body["currencies"]}
    assert types and currencies
    assert all(count > 0 for count in types.values())
    assert all(count > 0 for count in currencies.values())
    # Facets partition the current book.
    assert sum(types.values()) == body["total"]
    assert sum(currencies.values()) == body["total"]
    # Ordered by count descending, then value, so dropdowns render stably.
    for facet_list in (body["position_types"], body["currencies"]):
        counts = [facet["count"] for facet in facet_list]
        assert counts == sorted(counts, reverse=True)
        assert facet_list == sorted(facet_list, key=lambda f: (-f["count"], f["value"]))


def test_positions_and_facets_are_tenant_scoped(real_client: TestClient) -> None:
    for url in (POSITIONS_URL, FACETS_URL):
        response = real_client.get(url, headers=other_headers(), params={"limit": 1})
        assert response.status_code == 404, response.text
