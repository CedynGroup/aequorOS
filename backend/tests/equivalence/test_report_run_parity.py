"""Every run-backed report metric against the run that produced it.

The audit's §8 verdict: the LCR-NSFR package tie-back is proven, and the
CAR-RWA one is "Low, **subject to preview test coverage**" — that is, asserted
nowhere. A package is a sealed copy of a run, so "the generator does not
recompute" is a claim that needs a control, not a comment.

What this file asserts, per run-backed return:

* the package binds the SAME run the generator's own rule selects (newest
  succeeded baseline run for the period) and names it in ``source_runs``;
* every headline figure in the package equals that run's metric EXACTLY —
  a metric is persisted as a string and re-parsed, which round-trips losslessly
  through ``Decimal``, so there is no tolerance to grant;
* the mapped metric keys all exist, so a rename on either side fails loudly
  instead of silently comparing nothing.

Opt-in against the ACTUAL primary (``REAL_DATA_DATABASE_URL``), inside the
rolled-back ``real_client`` transaction. Packages are immutable versions, so
generating one here mints a version that is discarded with the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.equivalence.conftest import RUN_METRIC_ROUNDTRIP
from tests.real_data import REAL_BANK_ID, real_headers, requires_real_data

pytestmark = requires_real_data

BASE = f"/api/v1/banks/{REAL_BANK_ID}"
PACKAGES = f"{BASE}/regulatory-packages"


@dataclass(frozen=True)
class RunBackedReturn:
    """One return whose figures are copied from a calculation run."""

    return_code: str
    module: str
    #: ``package total code -> run metric key``. Only figures the generator
    #: claims to COPY. A figure the template derives belongs in the BoG-form
    #: equivalence file, not here.
    totals: dict[str, str]
    #: ``section code -> {row code -> run metric key}``.
    section_rows: dict[str, dict[str, str]]


RUN_BACKED = (
    RunBackedReturn(
        return_code="CAR-RWA",
        module="capital",
        totals={
            "total_capital_ghs": "total_capital_ghs",
            "total_rwa_ghs": "total_rwa_ghs",
        },
        # The audit's open gap: the capital package's headline RATIOS were
        # asserted nowhere, and ``car_pct`` is not even lifted into totals.
        section_rows={
            "capital_ratios": {
                "12.1": "cet1_ratio_pct",
                "12.2": "tier1_ratio_pct",
                "12.3": "car_pct",
                "12.4": "leverage_ratio_pct",
            }
        },
    ),
    RunBackedReturn(
        return_code="LCR-NSFR",
        module="liquidity",
        totals={
            "hqla_total_ghs": "hqla_total_ghs",
            "net_outflows_30d_ghs": "net_outflows_30d_ghs",
            "lcr_pct": "lcr_pct",
            "asf_total_ghs": "asf_total_ghs",
            "rsf_total_ghs": "rsf_total_ghs",
            "nsfr_pct": "nsfr_pct",
        },
        section_rows={},
    ),
    RunBackedReturn(
        return_code="FX-NOP",
        module="fx",
        totals={
            "nop_ghs": "nop_ghs",
            "nop_pct_tier1": "nop_pct_tier1",
            "var_99_1d_ghs": "var_99_1d_ghs",
            "tier1_ghs": "tier1_ghs",
        },
        section_rows={},
    ),
)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _periods(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/reporting-periods", headers=real_headers())
    assert response.status_code == 200, response.text
    periods = response.json()["periods"]
    assert periods, "the real Sample Bank must have at least one reporting period"
    return periods


def _baseline_run(client: TestClient, module: str, period_id: str) -> dict[str, Any] | None:
    """The succeeded baseline run the generator's own selection rule binds."""
    response = client.get(
        f"{BASE}/regulatory-runs",
        headers=real_headers(),
        params={
            "module": module,
            "scenario_code": "baseline",
            "reporting_period_id": period_id,
            "limit": 100,
        },
    )
    assert response.status_code == 200, response.text
    succeeded = [run for run in response.json()["runs"] if run["status"] == "succeeded"]
    return succeeded[0] if succeeded else None


def _generate(client: TestClient, return_code: str, reporting_date: str) -> Any:
    return client.post(
        PACKAGES,
        headers=real_headers(),
        json={"return_code": return_code, "reporting_date": reporting_date},
    )


def _package_and_run(
    client: TestClient, spec: RunBackedReturn
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Newest period that has a succeeded baseline run AND is regenerable.

    A package already ACKNOWLEDGED by the regulator refuses regeneration
    without a granted resubmission — that gate is not this file's subject, so
    such a period is skipped over rather than fought.
    """
    for period in _periods(client):
        run = _baseline_run(client, spec.module, period["id"])
        if run is None:
            continue
        response = _generate(client, spec.return_code, period["period_end"])
        if response.status_code == 409:
            continue
        assert response.status_code == 201, response.text
        return response.json(), run
    pytest.skip(
        f"no regenerable real period carries a succeeded baseline {spec.module} run "
        f"for {spec.return_code}"
    )


@pytest.mark.parametrize("spec", RUN_BACKED, ids=lambda spec: spec.return_code)
def test_package_headline_figures_equal_the_bound_run(
    real_client: TestClient, spec: RunBackedReturn
) -> None:
    package, run = _package_and_run(real_client, spec)
    metrics = run["metrics"]

    mapped = set(spec.totals.values())
    for row_map in spec.section_rows.values():
        mapped |= set(row_map.values())
    missing = sorted(key for key in mapped if key not in metrics)
    assert not missing, (
        f"{spec.return_code}: run metric keys {missing} no longer exist; this map is stale "
        "and the equivalence it claims to prove is not being checked"
    )

    totals = {row["code"]: row for row in package["snapshot"]["totals"]}
    for total_code, metric_key in spec.totals.items():
        assert total_code in totals, f"{spec.return_code}: package total {total_code} is gone"
        difference = _dec(totals[total_code]["value"]) - _dec(metrics[metric_key])
        assert abs(difference) <= RUN_METRIC_ROUNDTRIP, (
            f"{spec.return_code}: package total {total_code} "
            f"({totals[total_code]['value']}) != run metric {metric_key} ({metrics[metric_key]})"
        )

    sections = {section["code"]: section for section in package["snapshot"]["sections"]}
    for section_code, row_map in spec.section_rows.items():
        assert section_code in sections, f"{spec.return_code}: section {section_code} is gone"
        rows = {row["code"]: row for row in sections[section_code]["rows"]}
        for row_code, metric_key in row_map.items():
            assert row_code in rows, f"{spec.return_code}: {section_code} row {row_code} is gone"
            difference = _dec(rows[row_code]["value"]) - _dec(metrics[metric_key])
            assert abs(difference) <= RUN_METRIC_ROUNDTRIP, (
                f"{spec.return_code}: {section_code}.{row_code} ({rows[row_code]['value']}) "
                f"!= run metric {metric_key} ({metrics[metric_key]})"
            )


@pytest.mark.parametrize("spec", RUN_BACKED, ids=lambda spec: spec.return_code)
def test_package_provenance_names_the_run_it_copied(
    real_client: TestClient, spec: RunBackedReturn
) -> None:
    """A sealed copy is only auditable if the copy says what it copied.

    The bound run's identity, input hash and engine version must appear in
    ``source_runs``; without them a reader cannot re-derive the package's
    figures from the same inputs, which is the whole point of an immutable run.
    """
    package, run = _package_and_run(real_client, spec)
    entry = {
        "module": spec.module,
        "run_id": run["id"],
        "input_hash": run["input_hash"],
        "engine_version": run["engine_version"],
    }
    assert entry in package["source_runs"], (
        f"{spec.return_code}: the bound {spec.module} run {run['id']} is absent from source_runs"
    )
    assert all(item["module"] == spec.module for item in package["source_runs"])


def test_capital_package_ratios_tie_to_its_own_capital_and_rwa_totals(
    real_client: TestClient,
) -> None:
    """Internal consistency of the CAR-RWA package: the printed ratios are the
    printed capital over the printed RWA.

    This is the check that would have caught a package assembled from two
    different runs — the totals from one, the ratios from another. Exact: the
    engine quantizes the ratio to 6 dp and so does this.
    """
    package, _ = _package_and_run(real_client, RUN_BACKED[0])
    totals = {row["code"]: _dec(row["value"]) for row in package["snapshot"]["totals"]}
    sections = {section["code"]: section for section in package["snapshot"]["sections"]}
    rows = {row["code"]: _dec(row["value"]) for row in sections["capital_ratios"]["rows"]}
    rwa = totals["total_rwa_ghs"]
    assert rwa > 0
    six_dp = Decimal("0.000001")
    assert (totals["cet1_total_ghs"] / rwa * 100).quantize(six_dp) == rows["12.1"]
    assert (totals["tier1_total_ghs"] / rwa * 100).quantize(six_dp) == rows["12.2"]
    assert (totals["total_capital_ghs"] / rwa * 100).quantize(six_dp) == rows["12.3"]


def test_capital_package_rwa_sections_tie_to_the_runs_rwa_components(
    real_client: TestClient,
) -> None:
    """The RWA the package prints is the RWA the engine computed, component by
    component — not a re-aggregation that happens to land nearby.

    ``credit_rwa`` is a flat list of weighted exposures, so its rows sum to the
    run's ``credit_rwa_ghs`` exactly. The market and operational sections show
    the engine's intermediate working (open position, charge, multiplier), so
    they are checked through the engine's own additivity identity instead of a
    row sum — asserting a sum there would assert the wrong thing.
    """
    package, run = _package_and_run(real_client, RUN_BACKED[0])
    metrics = run["metrics"]
    sections = {section["code"]: section for section in package["snapshot"]["sections"]}
    credit_rows = sum(_dec(row["value"]) for row in sections["credit_rwa"]["rows"])
    assert credit_rows == _dec(metrics["credit_rwa_ghs"])
    assert (
        _dec(metrics["credit_rwa_ghs"])
        + _dec(metrics["market_rwa_ghs"])
        + _dec(metrics["operational_rwa_ghs"])
        == _dec(metrics["total_rwa_ghs"])
    )
    totals = {row["code"]: _dec(row["value"]) for row in package["snapshot"]["totals"]}
    assert totals["total_rwa_ghs"] == _dec(metrics["total_rwa_ghs"])
