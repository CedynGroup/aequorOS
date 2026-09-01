"""Credit / Loan Book module API (credit PR-2) — hermetic route-level tests.

Assertions are invariants over the compact canonical fixture (7 LOANs, one
stage-3), never golden magnitudes: the dashboard's ratio is the classification
engine's own NPL over gross; the resolved prudential limit is the seeded
Notice 2025/23 value (10%); the blotter's page arithmetic and facet counts are
internally consistent; the graceful-empty envelope answers a loan-less tenant.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import BankReportingPeriod
from app.services import job_queue, pipeline
from tests.api.helpers import ORG_1, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

_BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}"


def _seed_and_refresh(db_client: TestClient) -> None:
    _ = db_client
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
        job = job_queue.enqueue(
            session,
            ORG_1,
            "pipeline_refresh",
            bank_id=SAMPLE_BANK_ID,
            payload={"as_of_date": FIXTURE_AS_OF.isoformat()},
        )
        session.commit()
        pipeline.run_refresh(session, job)
        session.commit()
    finally:
        session.close()


def test_credit_dashboard_classifies_the_book_against_the_governed_limit(
    db_client: TestClient,
) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/credit/dashboard", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    metrics = body["metrics"]

    # The ratio is the engine's own arithmetic over its own figures.
    gross = Decimal(metrics["gross_loans_ghs"])
    npl = Decimal(metrics["npl_exposure_ghs"])
    assert gross > 0
    # The engine quantizes the FRACTION at 1e-6 before the wire multiplies by 100.
    expected = (npl / gross).quantize(Decimal("0.000001")) * 100
    assert Decimal(metrics["npl_ratio_pct"]) == expected

    # Notice 2025/23 parameters resolve from the control plane, never a literal.
    assert Decimal(metrics["npl_limit_pct"]) == Decimal("10")
    assert Decimal(metrics["npl_restriction_level_pct"]) == Decimal("15")
    assert metrics["npl_status"] in {"green", "amber", "red"}

    # The bank fixture binds the 5-grade grid; the grades roll up to gross.
    assert body["institution_class"] == "bank"
    grades = {bucket["grade"] for bucket in body["grades"]}
    assert "olem" in grades
    total = sum(Decimal(bucket["exposure_ghs"]) for bucket in body["grades"])
    assert total + Decimal(metrics["unclassified_exposure_ghs"]) >= gross

    # Provisions HELD come from the stated attributes (PR-1), so coverage is real.
    assert body["metrics"]["provisions_held"] is not None
    assert Decimal(metrics["provision_coverage_pct"]) > 0

    rules = {row["rule_code"] for row in body["validations"]}
    assert "npl_ratio_within_limit" in rules

    # The live engine ran, so the dashboard carries the live block for credit.
    assert body["live"] is not None
    assert body["live"]["module"] == "credit"


def test_credit_loans_blotter_pages_and_facets_are_consistent(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    page = db_client.get(f"{_BASE}/credit/loans?limit=3", headers=headers())
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["limit"] == 3
    assert len(body["rows"]) == 3
    assert body["filtered"] == body["total"]

    # Grade filter narrows to exactly the facet's count for that grade.
    facets = db_client.get(f"{_BASE}/credit/loans/facets", headers=headers()).json()
    grade_counts = {facet["value"]: facet["count"] for facet in facets["grades"]}
    assert sum(grade_counts.values()) == body["total"]
    grade, count = next(iter(sorted(grade_counts.items())))
    filtered = db_client.get(
        f"{_BASE}/credit/loans?grade={grade}&limit=500", headers=headers()
    ).json()
    assert filtered["filtered"] == count
    assert all(row["grade"] == grade for row in filtered["rows"])

    # Every row's classification is self-consistent.
    for row in body["rows"]:
        assert row["classification_basis"] in {"days_past_due", "stage_proxy", "unclassified"}
        assert Decimal(row["provision_required_ghs"]) >= 0


def test_credit_official_run_seals_a_reproducible_baseline(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    session = get_sessionmaker()()
    try:
        period_id = session.scalar(
            select(BankReportingPeriod.id).where(
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
                BankReportingPeriod.period_end == FIXTURE_AS_OF,
            )
        )
    finally:
        session.close()
    assert period_id is not None

    response = db_client.post(
        f"{_BASE}/credit/run-all-scenarios",
        headers=headers(),
        json={"reporting_period_id": str(period_id)},
    )
    assert response.status_code == 201, response.text
    runs = response.json()["runs"]
    assert [run["scenario_code"] for run in runs] == ["baseline"]
    run = runs[0]
    assert run["status"] == "succeeded"
    assert run["module"] == "credit"

    # The dashboard for the same period reports the sealed run as provenance,
    # and the live hash equals the sealed hash on an unchanged book.
    dashboard = db_client.get(
        f"{_BASE}/credit/dashboard?reporting_period_id={period_id}", headers=headers()
    ).json()
    assert dashboard["stored"] is True
    assert dashboard["latest_run_id"] == run["id"]
    live = dashboard["live"]
    live_hash = live.get("computed_from_input_hash") or live.get("computedFromInputHash")
    assert live_hash == run["input_hash"]


def test_credit_module_is_gracefully_empty_without_a_loan_book(db_client: TestClient) -> None:
    """A tenant with no ingested book gets the availability envelope, not a 500."""
    response = db_client.get(f"{_BASE}/credit/dashboard", headers=headers())
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        body = response.json()
        assert body.get("available") is False
        assert body.get("error_code")


def test_concentration_monitor_measures_dimensions_with_honest_coverage(
    db_client: TestClient,
) -> None:
    _seed_and_refresh(db_client)
    response = db_client.get(f"{_BASE}/credit/concentration", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    dimensions = {d["dimension"]: d for d in body["dimensions"]}
    assert set(dimensions) == {
        "single_name",
        "sector",
        "geography",
        "product",
        "collateral",
        "employer",
    }
    # No employer attributes on the fixture book: zero buckets, zero coverage —
    # and no fabricated "Unknown" bucket.
    employer = dimensions["employer"]
    assert employer["bucket_count"] == 0
    assert Decimal(employer["coverage_pct"]) == 0
    # Single name always covers the whole book (every row has an identity).
    assert Decimal(dimensions["single_name"]["coverage_pct"]) == 100
    # No Board limits configured yet: every bucket reads not_set, none breach.
    assert body["limit_count"] == 0
    assert body["breaches"] == []
    statuses = {
        bucket["limit_status"]
        for dimension in body["dimensions"]
        for bucket in dimension["buckets"]
    }
    assert statuses <= {"not_set"}
    assert body["capital_basis"] in {"tier1", "net_own_funds"}


def test_concentration_limits_register_roundtrip(db_client: TestClient) -> None:
    _seed_and_refresh(db_client)
    put = db_client.put(
        f"{_BASE}/concentration-limits",
        headers=headers(),
        json={
            "effective_from": "2026-01-01",
            "approved_by": "Board Credit Committee",
            "reason": "Initial limit structure per the concentration guidelines",
            "limits": [
                {"dimension": "single_name", "limit_kind": "share_of_capital_pct", "value": "25"},
                {"dimension": "employer", "limit_kind": "share_of_book_pct", "value": "20"},
            ],
        },
    )
    assert put.status_code == 200, put.text
    register = put.json()
    assert len(register["limits"]) == 2

    monitor = db_client.get(f"{_BASE}/credit/concentration", headers=headers()).json()
    assert monitor["limit_count"] == 2
