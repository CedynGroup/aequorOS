"""Board threshold register API (LMTD 2026 ¶11(b)–(e)).

The register resolves regulatory defaults until a Board generation exists,
records generations effective-dated with approval evidence, keeps history,
rejects unknown codes and non-positive floors, and gates writes at approver.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.services.liquidity_thresholds import BANK_MINIMUM_PCT
from tests.api.helpers import ORG_2, headers
from tests.api.test_ingestion import seed_bank

URL = "/api/v1/banks/{bank_id}/liquidity-thresholds"


def test_defaults_resolve_before_any_board_row(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    response = db_client.get(URL.format(bank_id=bank_id), headers=headers())
    assert response.status_code == 200, response.text
    register = response.json()
    assert register["jurisdiction_code"] == "GH"
    by_code = {row["threshold_code"]: row for row in register["thresholds"]}
    assert set(by_code) == set(BANK_MINIMUM_PCT)
    assert all(row["source"] == "regulatory_default" for row in by_code.values())
    assert Decimal(by_code["narrow_to_volatile"]["threshold_pct"]) == Decimal("80")
    assert register["history"] == []


def test_board_generations_supersede_and_keep_history(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    first = db_client.put(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={
            "effective_from": "2026-04-01",
            "approved_by": "Board minute 2026-14",
            "thresholds": {"narrow_to_volatile": "85"},
            "reason": "Annual Board review adopted a higher internal floor.",
        },
    )
    assert first.status_code == 200, first.text
    by_code = {row["threshold_code"]: row for row in first.json()["thresholds"]}
    assert by_code["narrow_to_volatile"]["source"] == "board_register"
    assert Decimal(by_code["narrow_to_volatile"]["threshold_pct"]) == Decimal("85")
    assert by_code["narrow_to_volatile"]["approved_by"] == "Board minute 2026-14"
    # Untouched codes still resolve to the regulatory default.
    assert by_code["broad_to_volatile"]["source"] == "regulatory_default"

    second = db_client.put(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={
            "effective_from": "2026-06-01",
            "approved_by": "Board minute 2026-22",
            "thresholds": {"narrow_to_volatile": "90"},
            "reason": "Mid-year revision.",
        },
    )
    assert second.status_code == 200, second.text
    history = second.json()["history"]
    assert len(history) == 2
    generations = {row["effective_from"]: row for row in history}
    assert generations["2026-04-01"]["effective_to"] == "2026-06-01"  # closed
    assert generations["2026-06-01"]["effective_to"] is None  # open

    # Resolution is as-of-date aware: April sees 85, June sees 90.
    april = db_client.get(
        URL.format(bank_id=bank_id) + "?as_of=2026-04-30", headers=headers()
    ).json()
    june = db_client.get(
        URL.format(bank_id=bank_id) + "?as_of=2026-06-30", headers=headers()
    ).json()
    april_row = next(
        row for row in april["thresholds"] if row["threshold_code"] == "narrow_to_volatile"
    )
    june_row = next(
        row for row in june["thresholds"] if row["threshold_code"] == "narrow_to_volatile"
    )
    assert Decimal(april_row["threshold_pct"]) == Decimal("85")
    assert Decimal(june_row["threshold_pct"]) == Decimal("90")


def test_validation_and_authorization_guards(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    unknown = db_client.put(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={
            "effective_from": "2026-04-01",
            "approved_by": "Board minute",
            "thresholds": {"made_up_ratio": "50"},
            "reason": "typo",
        },
    )
    assert unknown.status_code == 422
    assert "made_up_ratio" in unknown.json()["error"]["message"]

    non_positive = db_client.put(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("approver",)),
        json={
            "effective_from": "2026-04-01",
            "approved_by": "Board minute",
            "thresholds": {"narrow_to_volatile": "0"},
            "reason": "zero floor",
        },
    )
    assert non_positive.status_code == 422

    analyst = db_client.put(
        URL.format(bank_id=bank_id),
        headers=headers(roles=("analyst",)),
        json={
            "effective_from": "2026-04-01",
            "approved_by": "Board minute",
            "thresholds": {"narrow_to_volatile": "85"},
            "reason": "analyst attempt",
        },
    )
    assert analyst.status_code == 403

    foreign = db_client.get(URL.format(bank_id=bank_id), headers=headers(ORG_2))
    assert foreign.status_code == 404
