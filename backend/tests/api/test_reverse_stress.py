"""Reverse stress frontier (Phase 2 item 4).

The frontier persists as an immutable RegulatoryRun (module reverse_stress)
anchored to both engines' baseline input hashes; the search scales the
combined liquidity scenario and the severe capital scenario by bisection.
Golden frontier multipliers are minted from the deterministic seeded book.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.api.helpers import ORG_2, headers
from tests.api.test_ingestion import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/reverse-stress/runs"
LATEST_URL = "/api/v1/banks/{bank_id}/reverse-stress/latest"


def _period_id(client: TestClient, bank_id: str) -> str:
    response = client.get(f"/api/v1/banks/{bank_id}/reporting-periods", headers=headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    return next(p["id"] for p in periods if p["period_end"] == "2026-03-31")


def test_reverse_stress_persists_frontier_with_provenance(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    period_id = _period_id(db_client, bank_id)

    response = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert response.status_code == 201, response.text
    frontier = response.json()
    assert len(frontier["input_hash"]) == 64
    assert frontier["engine_version"] == "reverse-stress-v1.0.0"

    # The seeded book's combined scenario already lands at LCR 87.36% < 100,
    # so the liquidity frontier sits below 1.0x — the bisection must find it.
    liquidity = frontier["liquidity_axis"]
    assert liquidity["breached"] is True
    assert Decimal(liquidity["breach_multiplier"]) < Decimal("1")
    assert Decimal(liquidity["lcr_at_breach_pct"]) < Decimal(liquidity["lcr_min_pct"])

    capital = frontier["capital_axis"]
    assert Decimal(capital["cet1_min_pct"]) == Decimal("6.5")
    if capital["breached"]:
        assert Decimal(capital["worst_cet1_at_breach_pct"]) < Decimal("6.5")
    else:
        assert Decimal(capital["worst_cet1_at_k_max_pct"]) >= Decimal("6.5")
    assert "Liquidity:" in frontier["narrative"] and "Capital:" in frontier["narrative"]

    # Determinism: a rerun over the same book anchors to the same hash.
    again = db_client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={"reporting_period_id": period_id},
    )
    assert again.status_code == 201
    assert again.json()["input_hash"] == frontier["input_hash"]
    assert again.json()["liquidity_axis"] == liquidity

    latest = db_client.get(
        LATEST_URL.format(bank_id=bank_id) + f"?reporting_period_id={period_id}",
        headers=headers(),
    )
    assert latest.status_code == 200
    assert latest.json()["run_id"] == again.json()["run_id"]

    foreign = db_client.get(
        LATEST_URL.format(bank_id=bank_id) + f"?reporting_period_id={period_id}",
        headers=headers(ORG_2),
    )
    assert foreign.status_code == 404
