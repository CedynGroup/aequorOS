"""Data activation endpoint against the ACTUAL primary database.

Invariants: an activation of the real book rebuilds the period's fact set from
canonical data (deleted = the prior count, created = the new count = the sum of
the derived groups' rows) and, when asked, runs the whole official-module sweep
(liquidity 5 / capital 4 / IRR 7 / FX 4 / FTP 3 scenarios + 1 forecast) minting
NEW immutable runs on top of the untouched history, with per-module statuses
that follow from the per-scenario counts; a derivation-only activation mints no
runs; a date without canonical snapshots is a 409; tenant isolation.

COST: the full sweep over the real book (~570k positions) takes ~14 minutes and
a bare derivation ~2.5 minutes, so this file performs exactly ONE calculating
activation and ONE derivation-only activation.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BankFinancialFact
from tests.real_data import (
    REAL_BANK_ID,
    REAL_ORG_ID,
    other_headers,
    real_headers,
    requires_real_data,
)

pytestmark = requires_real_data

ACTIVATIONS_URL = f"/api/v1/banks/{REAL_BANK_ID}/data-activations"
RUNS_URL = f"/api/v1/banks/{REAL_BANK_ID}/regulatory-runs"
ALL_MODULES = ["liquidity", "capital", "irr", "fx", "ftp", "forecast"]
# Scenario batch sizes are engine constants (module registries), not book data.
SCENARIOS_PER_MODULE = {
    "liquidity": 5,  # baseline, idiosyncratic, market_wide, combined, usd_funding_stress
    "capital": 4,
    "irr": 7,
    "fx": 4,
    "ftp": 3,
    "forecast": 1,
}
HEADLINE_PREFIX = {
    "liquidity": "LCR ",
    "capital": "CAR ",
    "irr": "worst ΔEVE/Tier1 ",
    "fx": "NOP/Tier1 ",
    "ftp": "portfolio NIM ",
    "forecast": "avg ROE ",
}
# The groups every module's baseline consumes; a book that lights up all six
# dashboards must have derived each of them.
MODULE_FEEDING_GROUPS = {
    "balance_sheet",
    "loan_exposure",
    "securities",
    "lcr_inflow",
    "operational_income",
    "capital_component",
    "irr_position",
    "fx_position",
    "ftp_curve_point",
    "ftp_product",
}


def _latest_period(client: TestClient) -> dict[str, Any]:
    response = client.get(f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods[0]


def _fact_count(session: Session, period_id: str) -> int:
    session.info["organization_id"] = REAL_ORG_ID
    return int(
        session.scalar(
            select(func.count())
            .select_from(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == REAL_ORG_ID,
                BankFinancialFact.bank_id == REAL_BANK_ID,
                BankFinancialFact.reporting_period_id == period_id,
            )
        )
        or 0
    )


def _module_totals(client: TestClient, period_id: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for module in ALL_MODULES:
        response = client.get(
            RUNS_URL,
            headers=real_headers(),
            params={"module": module, "reporting_period_id": period_id},
        )
        assert response.status_code == 200, response.text
        totals[module] = int(response.json()["total"])
    return totals


def _newest_run(client: TestClient, module: str, period_id: str) -> dict[str, Any] | None:
    response = client.get(
        RUNS_URL,
        headers=real_headers(),
        params={"module": module, "reporting_period_id": period_id},
    )
    assert response.status_code == 200, response.text
    runs = response.json()["runs"]
    if not runs:
        return None
    detail = client.get(f"{RUNS_URL}/{runs[0]['id']}", headers=real_headers())
    assert detail.status_code == 200, detail.text
    return detail.json()


def _activate(
    client: TestClient, as_of_date: str, *, run_calculations: bool = True
) -> dict[str, Any]:
    response = client.post(
        ACTIVATIONS_URL,
        headers=real_headers(),
        json={
            "as_of_date": as_of_date,
            "reason": "Activate the real book (transaction-isolated test).",
            "run_calculations": run_calculations,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assert_derivation_rebuilt_period(
    body: dict[str, Any], period: dict[str, Any], *, facts_before: int, facts_after: int
) -> None:
    # The real bank already reports on this period end: the activation OWNS the
    # existing period rather than minting a duplicate, and rebuilds its facts.
    assert body["bank_id"] == REAL_BANK_ID
    assert body["reporting_period_id"] == period["id"]
    assert body["period_label"] == period["label"]
    assert body["as_of_date"] == period["period_end"]
    assert body["period_created"] is False
    assert body["facts_deleted"] == facts_before
    assert body["facts_created"] == facts_after
    assert body["facts_created"] > 0
    groups = {group["group"]: group for group in body["groups"]}
    assert len(groups) == len(body["groups"])  # each group reported once
    derived = {name for name, group in groups.items() if group["status"] == "derived"}
    skipped = {name for name, group in groups.items() if group["status"] == "skipped"}
    assert derived | skipped == set(groups)
    assert derived >= MODULE_FEEDING_GROUPS
    # Every derived group contributes its rows; nothing else creates facts.
    assert all(groups[name]["rows"] > 0 for name in derived)
    assert sum(groups[name]["rows"] for name in derived) == body["facts_created"]
    # A skipped group always says why.
    assert all(groups[name]["note"] for name in skipped)
    assert all(isinstance(warning, str) and warning for warning in body["warnings"])


def test_activation_derives_facts_and_runs_all_six_modules(  # noqa: PLR0915
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    facts_before = _fact_count(real_session, period["id"])
    totals_before = _module_totals(real_client, period["id"])
    activations_before = real_client.get(ACTIVATIONS_URL, headers=real_headers()).json()
    prior_liquidity = _newest_run(real_client, "liquidity", period["id"])

    # ONE full sweep — see the module docstring.
    body = _activate(real_client, period["period_end"])
    facts_after = _fact_count(real_session, period["id"])
    _assert_derivation_rebuilt_period(
        body, period, facts_before=facts_before, facts_after=facts_after
    )

    runs = {run["module"]: run for run in body["runs"]}
    assert list(runs) == ALL_MODULES
    for module, run in runs.items():
        succeeded, failed = run["scenarios_succeeded"], run["scenarios_failed"]
        # Status is a pure function of the per-scenario outcome counts.
        assert succeeded + failed == SCENARIOS_PER_MODULE[module], module
        if failed == 0:
            assert run["status"] == "succeeded", module
        elif succeeded == 0:
            assert run["status"] == "failed", module
        else:
            assert run["status"] == "partial", module
        assert (run["error"] is None) is (failed == 0), module
        # The real book supports every module: each baseline (or the forecast)
        # succeeded and carries its headline metric.
        assert run["status"] in {"succeeded", "partial"}, (module, run["error"])
        assert run["headline"] is not None, module
        assert run["headline"].startswith(HEADLINE_PREFIX[module]), module

    # Run history is immutable and append-only: every module gained exactly its
    # scenario batch (failed scenarios persist as failed runs), and the newest
    # pre-existing run is untouched.
    totals_after = _module_totals(real_client, period["id"])
    for module in ALL_MODULES:
        run = runs[module]
        assert totals_after[module] == (
            totals_before[module] + run["scenarios_succeeded"] + run["scenarios_failed"]
        ), module
    if prior_liquidity is not None:
        again = real_client.get(f"{RUNS_URL}/{prior_liquidity['id']}", headers=real_headers())
        assert again.status_code == 200
        assert again.json()["input_hash"] == prior_liquidity["input_hash"]
        assert again.json()["status"] == prior_liquidity["status"]
        assert again.json()["metrics"] == prior_liquidity["metrics"]
    newest_liquidity = _newest_run(real_client, "liquidity", period["id"])
    assert newest_liquidity is not None
    assert prior_liquidity is None or newest_liquidity["id"] != prior_liquidity["id"]
    assert newest_liquidity["reporting_period_id"] == period["id"]

    # The period is visible to the dashboards and each module answers 200.
    for path in (
        f"/api/v1/banks/{REAL_BANK_ID}/liquidity/dashboard",
        f"/api/v1/banks/{REAL_BANK_ID}/capital/dashboard",
        f"/api/v1/banks/{REAL_BANK_ID}/irr/dashboard",
        f"/api/v1/banks/{REAL_BANK_ID}/fx/dashboard",
        f"/api/v1/banks/{REAL_BANK_ID}/ftp/dashboard",
    ):
        response = real_client.get(
            path, headers=real_headers(), params={"reporting_period_id": period["id"]}
        )
        assert response.status_code == 200, f"{path}: {response.text}"

    # The activation is listed from its audit trail, newest first.
    listing = real_client.get(ACTIVATIONS_URL, headers=real_headers())
    assert listing.status_code == 200
    activations = listing.json()["activations"]
    assert len(activations) == min(len(activations_before["activations"]) + 1, 10)
    latest = activations[0]
    assert latest["period_label"] == period["label"]
    assert latest["as_of_date"] == period["period_end"]
    assert latest["facts_created"] == body["facts_created"]
    assert latest["modules_succeeded"] == sum(
        1 for run in body["runs"] if run["status"] == "succeeded"
    )
    assert latest["modules_failed"] == sum(1 for run in body["runs"] if run["status"] == "failed")
    assert latest["warnings"] == len(body["warnings"])


def test_activation_without_calculations_only_derives(
    real_client: TestClient, real_session: Session
) -> None:
    period = _latest_period(real_client)
    facts_before = _fact_count(real_session, period["id"])
    totals_before = _module_totals(real_client, period["id"])

    body = _activate(real_client, period["period_end"], run_calculations=False)
    facts_after = _fact_count(real_session, period["id"])
    _assert_derivation_rebuilt_period(
        body, period, facts_before=facts_before, facts_after=facts_after
    )
    assert body["runs"] == []
    # No module was recomputed: not a single run was minted for the period.
    assert _module_totals(real_client, period["id"]) == totals_before

    latest = real_client.get(ACTIVATIONS_URL, headers=real_headers()).json()["activations"][0]
    assert latest["facts_created"] == body["facts_created"]
    assert latest["modules_succeeded"] == 0
    assert latest["modules_failed"] == 0


def test_activation_conflicts_without_canonical_data(real_client: TestClient) -> None:
    # No canonical position snapshot exists for a far-future as-of date.
    future = datetime.date(2099, 12, 31).isoformat()
    response = real_client.post(
        ACTIVATIONS_URL,
        headers=real_headers(),
        json={"as_of_date": future, "reason": "Nothing was ingested."},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "no_canonical_data"
    # The refusal minted no period.
    labels = {
        item["label"]
        for item in real_client.get(
            f"/api/v1/banks/{REAL_BANK_ID}/reporting-periods", headers=real_headers()
        ).json()["periods"]
    }
    assert "2099-12" not in labels

    # The audited reason is required.
    missing_reason = real_client.post(
        ACTIVATIONS_URL,
        headers=real_headers(),
        json={"as_of_date": _latest_period(real_client)["period_end"]},
    )
    assert missing_reason.status_code == 422


def test_activation_is_tenant_scoped(real_client: TestClient) -> None:
    period = _latest_period(real_client)
    response = real_client.post(
        ACTIVATIONS_URL,
        headers=other_headers(),
        json={"as_of_date": period["period_end"], "reason": "Cross-tenant attempt."},
    )
    assert response.status_code == 404, response.text
    listing = real_client.get(ACTIVATIONS_URL, headers=other_headers())
    assert listing.status_code == 404
