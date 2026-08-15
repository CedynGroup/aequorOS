"""The desk console API: auth gate, two-track workflows, audit rows.

Every mutation must land an ``operator_audit_log`` row, dual control must
hold at the API boundary (the operator's own email is the actor), and
publish must fan out through the adapter seam into canonical state.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bank,
    CanonicalYieldCurve,
    DeskDetermination,
    DeskSourceCapture,
    OperatorAuditLog,
    Organization,
)
from tests.operator.conftest import operator_headers
from tests.storage.inmemory import InMemoryStorageClient

BASE = "/operator/v1/desk"
DEV_EMAIL = "dev@aequoros.com"
COB = "2026-08-07"

DERIVED_VALUES = {
    "qa_passed": True,
    "rates_qa_passed": True,
    "curves_qa_passed": True,
    "curves": {
        "AEQ.GHS.SOV.ZERO": {
            "curve_type": "zero",
            "points": [
                {"tenor_months": 3, "rate_pct": 24.5},
                {"tenor_months": 6, "rate_pct": 24.9},
            ],
        }
    },
    "rates": {
        "GHS.MPR": {"value": "15.000000", "unit": "pct", "treatment": "pass_through"},
        "GHS.INTERBANK.ON": {
            "value": "10.000000",
            "unit": "pct",
            "treatment": "windowed",
        },
        "GHS.GRR": {"value": "21.300000", "unit": "pct", "treatment": "pass_through"},
    },
    "reference_rates": {"GHS.MPR": "15.0", "GHS.GRR": "21.3"},
    "fx_rates": {"USD/GHS": "12.85"},
}


def _audit_actions(db: Session) -> list[str]:
    return list(db.scalars(select(OperatorAuditLog.action)))


def _bootstrap_methodology(client: TestClient) -> None:
    seeded = client.post(f"{BASE}/methodologies/ensure-default", headers=operator_headers())
    assert seeded.status_code == 201, seeded.text
    approved = client.post(
        f"{BASE}/methodologies/AEQ-GHS-CURVES/versions/1/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-01-01"},
    )
    assert approved.status_code == 200, approved.text


def _enter_observation(client: TestClient, value: str = "15.00") -> None:
    response = client.post(
        f"{BASE}/observations",
        headers=operator_headers(),
        json={
            "series_code": "GHS.MPR",
            "as_of_date": "2026-07-22",
            "value": value,
            "unit": "pct",
        },
    )
    assert response.status_code == 201, response.text


def _draft_determination(client: TestClient) -> dict:
    response = client.post(
        f"{BASE}/determinations", headers=operator_headers(), json={"cob_date": COB}
    )
    assert response.status_code == 201, response.text
    return response.json()


# -- auth gate -----------------------------------------------------------------------


def test_desk_endpoints_require_operator_auth(operator_client: TestClient) -> None:
    assert operator_client.get(f"{BASE}/methodologies").status_code == 401
    assert (
        operator_client.post(f"{BASE}/determinations", json={"cob_date": COB}).status_code
        == 401
    )
    assert (
        operator_client.get(
            f"{BASE}/methodologies", headers=operator_headers("wrong-token")
        ).status_code
        == 401
    )


# -- methodology register (Track 2) -----------------------------------------------------


def test_methodology_lifecycle_with_dual_control_and_audit(
    operator_client: TestClient, operator_db: Session
) -> None:
    created = operator_client.post(
        f"{BASE}/methodologies/ensure-default", headers=operator_headers()
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["methodology_code"] == "AEQ-GHS-CURVES"
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert "grr_formula" in body["parameters"]

    # Idempotent bootstrap: same row, no duplicate.
    again = operator_client.post(
        f"{BASE}/methodologies/ensure-default", headers=operator_headers()
    )
    assert again.json()["id"] == body["id"]
    listing = operator_client.get(f"{BASE}/methodologies", headers=operator_headers())
    assert listing.json()["total"] == 1

    # The dev operator approves the bootstrap draft (proposer is the system
    # identity, so dual control holds).
    approved = operator_client.post(
        f"{BASE}/methodologies/AEQ-GHS-CURVES/versions/1/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-01-01"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == DEV_EMAIL

    # Track 2: the same operator proposes v2 ...
    proposed = operator_client.post(
        f"{BASE}/methodologies/AEQ-GHS-CURVES/versions",
        headers=operator_headers(),
        json={
            "parameters": {"min_trade_count": 4},
            "change_rationale": "recalibrated liquidity gate",
        },
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["version"] == 2
    assert proposed.json()["proposed_by"] == DEV_EMAIL

    # ... and therefore CANNOT approve it (dual control at the API boundary).
    self_approve = operator_client.post(
        f"{BASE}/methodologies/AEQ-GHS-CURVES/versions/2/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-09-01"},
    )
    assert self_approve.status_code == 422

    actions = _audit_actions(operator_db)
    assert actions.count("desk.methodology.ensure_default") == 2
    assert "desk.methodology.approve_version" in actions
    assert "desk.methodology.propose_version" in actions


# -- observations ------------------------------------------------------------------------


def test_manual_observation_entry_and_append_only_correction(
    operator_client: TestClient, operator_db: Session
) -> None:
    _enter_observation(operator_client, "15.00")
    _enter_observation(operator_client, "15.25")  # correction supersedes

    current = operator_client.get(
        f"{BASE}/observations",
        headers=operator_headers(),
        params={"series_code": "GHS.MPR"},
    ).json()
    assert current["total"] == 1
    assert Decimal(current["observations"][0]["value"]) == Decimal("15.25")
    assert current["observations"][0]["entered_by"] == DEV_EMAIL

    everything = operator_client.get(
        f"{BASE}/observations",
        headers=operator_headers(),
        params={"series_code": "GHS.MPR", "include_superseded": "true"},
    ).json()
    assert everything["total"] == 2

    assert _audit_actions(operator_db).count("desk.observation.manual_entry") == 2


def test_captures_listing(operator_client: TestClient, operator_db: Session) -> None:
    empty = operator_client.get(f"{BASE}/captures", headers=operator_headers())
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    operator_db.add(
        DeskSourceCapture(
            source_key="bog_tbill_rates",
            as_of_date=date(2026, 8, 7),
            content_sha256="0" * 64,
            payload={"rows": []},
            parser_version="bog_wdt_v1",
            status="parsed",
            created_by=DEV_EMAIL,
        )
    )
    operator_db.commit()

    listed = operator_client.get(f"{BASE}/captures", headers=operator_headers()).json()
    assert listed["total"] == 1
    assert listed["captures"][0]["source_key"] == "bog_tbill_rates"


# -- determinations (Track 1) ---------------------------------------------------------------


def test_determination_workflow_enforces_maker_checker(
    operator_client: TestClient, operator_db: Session
) -> None:
    _bootstrap_methodology(operator_client)
    _enter_observation(operator_client)

    draft = _draft_determination(operator_client)
    assert draft["status"] == "draft"
    assert draft["prepared_by"] == DEV_EMAIL
    assert len(draft["input_digest"]) == 64
    assert draft["input_snapshot"]

    # Seed a rates-ready package before submit (this test isolates maker-checker).
    row = operator_db.get(DeskDetermination, UUID(draft["id"]))
    assert row is not None
    row.derived_values = DERIVED_VALUES
    operator_db.commit()

    submitted = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/submit", headers=operator_headers()
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"

    # The preparer (this same operator identity) cannot approve.
    refused = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/approve", headers=operator_headers()
    )
    assert refused.status_code == 422

    # A different preparer's submission IS approvable by this operator.
    row = operator_db.get(DeskDetermination, UUID(draft["id"]))
    assert row is not None
    row.prepared_by = "analyst@aequoros.com"
    operator_db.commit()

    approved = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/approve", headers=operator_headers()
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["reviewed_by"] == DEV_EMAIL

    # Illegal transition through the API: rejecting an approved determination.
    rejected = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/reject",
        headers=operator_headers(),
        json={"reason": "too late"},
    )
    assert rejected.status_code == 409

    actions = _audit_actions(operator_db)
    for expected in (
        "desk.determination.create_draft",
        "desk.determination.submit",
        "desk.determination.approve",
    ):
        assert expected in actions


def test_determination_package_endpoint(
    operator_client: TestClient, operator_db: Session
) -> None:
    _bootstrap_methodology(operator_client)
    _enter_observation(operator_client)
    # Seed required companion series so completeness is partially populated.
    for series, value in (
        ("GHS.INTERBANK.ON", "10.00"),
        ("GHS.TBILL.91.DISCOUNT", "20.00"),
        ("GHS.TBILL.182.DISCOUNT", "21.00"),
        ("GHS.TBILL.364.DISCOUNT", "22.00"),
        ("GHS.USDGHS.MID", "12.50"),
        ("GHS.GRR", "21.30"),
    ):
        r = operator_client.post(
            f"{BASE}/observations",
            headers=operator_headers(),
            json={
                "series_code": series,
                "as_of_date": "2026-08-07" if series != "GHS.GRR" else "2026-07-01",
                "value": value,
                "unit": "rate" if series == "GHS.USDGHS.MID" else "pct",
            },
        )
        assert r.status_code == 201, r.text

    draft = _draft_determination(operator_client)
    pkg = operator_client.get(
        f"{BASE}/determinations/{draft['id']}/package", headers=operator_headers()
    )
    assert pkg.status_code == 200, pkg.text
    body = pkg.json()
    assert "completeness" in body
    assert body["completeness"]["ready"] is True
    assert "week_over_week" in body
    assert "input_provenance" in body
    assert isinstance(body["completeness"]["items"], list)
    assert body["completeness"]["required_total"] >= 1


def test_publish_fans_out_and_audits(
    operator_client: TestClient,
    operator_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_storage = InMemoryStorageClient()
    monkeypatch.setattr(
        "app.adapters.market_data.pull_runner.get_storage_client", lambda: client_storage
    )
    monkeypatch.setattr(
        "app.adapters.market_data.cache.get_storage_client", lambda: client_storage
    )

    organization = Organization(name="Desk Publish Tenant")
    operator_db.add(organization)
    operator_db.flush()
    bank = Bank(
        organization_id=organization.id,
        name="Desk Publish Bank",
        short_name="desk-publish",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    operator_db.add(bank)
    operator_db.commit()

    _bootstrap_methodology(operator_client)
    _enter_observation(operator_client)
    draft = _draft_determination(operator_client)
    row = operator_db.get(DeskDetermination, UUID(draft["id"]))
    assert row is not None
    row.prepared_by = "analyst@aequoros.com"
    row.derived_values = DERIVED_VALUES
    operator_db.commit()
    submitted = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/submit", headers=operator_headers()
    )
    assert submitted.status_code == 200, submitted.text
    approved = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/approve", headers=operator_headers()
    )
    assert approved.status_code == 200, approved.text

    published = operator_client.post(
        f"{BASE}/determinations/{draft['id']}/publish", headers=operator_headers()
    )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["status"] == "complete"
    assert body["published_by"] == DEV_EMAIL
    assert len(body["results"]) == 1
    assert body["results"][0]["bank_id"] == bank.id

    curve = operator_db.scalar(
        select(CanonicalYieldCurve).where(
            CanonicalYieldCurve.bank_id == bank.id,
            CanonicalYieldCurve.curve_name == "AEQ.GHS.SOV.ZERO",
            CanonicalYieldCurve.superseded_by.is_(None),
        )
    )
    assert curve is not None
    assert curve.source_system == "AEQUOR_DESK"

    listed = operator_client.get(
        f"{BASE}/publications",
        headers=operator_headers(),
        params={"determination_id": draft["id"]},
    ).json()
    assert listed["total"] == 1

    assert "desk.determination.publish" in _audit_actions(operator_db)
