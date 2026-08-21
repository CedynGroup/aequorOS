"""Operator API for the regulatory-parameter control plane (SDI Phase C).

Pins: reads are authenticated, changes are four-eyes at the API boundary (a
proposer cannot approve its own draft), an approved generation supersedes the
prior open one, and every mutation lands an ``operator_audit_log`` row.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OperatorAuditLog, RegulatoryParameter
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/regulatory-parameters"
DEV_EMAIL = "dev@aequoros.com"


def _actions(db: Session) -> list[str]:
    return list(db.scalars(select(OperatorAuditLog.action)))


def _propose_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "scope_type": "institution_class",
        "scope_key": "sdi",
        "param_code": "car_min",
        "value_numeric": "11",
        "unit": "percent",
        "source_citation": "test uplift",
        "confirmation_status": "confirmed",
        "effective_from": "2027-01-01",
        "change_rationale": "raise the SDI CAR floor",
    }
    body.update(overrides)
    return body


def test_list_returns_the_seeded_grid(operator_client: TestClient) -> None:
    resp = operator_client.get(BASE, headers=operator_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    codes = {p["param_code"] for p in body["parameters"]}
    assert {"car_min", "paid_up_min", "large_exposure_limit_pct", "narrow_to_volatile"} <= codes
    assert body["total"] == len(body["parameters"])
    # Both confirmed and pending rows surface (confirmation status is never hidden).
    assert {p["confirmation_status"] for p in body["parameters"]} == {"confirmed", "pending"}
    # Every row carries provenance.
    for p in body["parameters"]:
        assert p["source_citation"]
        assert p["status"] == "approved"


def test_read_requires_authentication(operator_client: TestClient) -> None:
    resp = operator_client.get(BASE, headers=operator_headers("wrong-token"))
    assert resp.status_code == 401


def test_filter_by_param_code(operator_client: TestClient) -> None:
    resp = operator_client.get(BASE, headers=operator_headers(), params={"param_code": "car_min"})
    assert resp.status_code == 200
    rows = resp.json()["parameters"]
    assert rows
    assert {r["param_code"] for r in rows} == {"car_min"}
    assert {r["scope_key"] for r in rows} == {"bank", "sdi"}


def test_propose_creates_a_draft(operator_client: TestClient) -> None:
    resp = operator_client.post(BASE, headers=operator_headers(), json=_propose_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "draft"
    assert body["proposed_by"] == DEV_EMAIL
    assert body["approved_by"] is None
    assert Decimal(body["value_numeric"]) == Decimal("11")


def test_proposer_cannot_self_approve(operator_client: TestClient) -> None:
    proposed = operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(effective_from="2027-02-01")
    )
    param_id = proposed.json()["id"]
    self_approve = operator_client.post(
        f"{BASE}/{param_id}/approve", headers=operator_headers(), json={}
    )
    assert self_approve.status_code == 422
    # The operator app wraps errors as {"error": {"message": ...}}.
    assert "four-eyes" in self_approve.json()["error"]["message"].lower()


def test_four_eyes_approval_supersedes_the_prior_generation(
    operator_client: TestClient, operator_db: Session
) -> None:
    # A draft proposed by a DIFFERENT operator (inserted directly), approved by dev.
    draft = RegulatoryParameter(
        scope_type="institution_class",
        scope_key="sdi",
        param_code="car_min",
        jurisdiction_code="GH",
        value_numeric=Decimal("12"),
        unit="percent",
        source_citation="hypothetical 2028 uplift",
        confirmation_status="confirmed",
        effective_from=date(2028, 1, 1),
        status="draft",
        proposed_by="maker@aequoros.com",
    )
    operator_db.add(draft)
    operator_db.commit()
    param_id = str(draft.id)

    approve = operator_client.post(
        f"{BASE}/{param_id}/approve", headers=operator_headers(), json={}
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"
    assert approve.json()["approved_by"] == DEV_EMAIL

    operator_db.expire_all()
    prior = operator_db.scalar(
        select(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "car_min",
            RegulatoryParameter.scope_key == "sdi",
            RegulatoryParameter.effective_from == date(2020, 1, 1),
        )
    )
    assert prior is not None
    assert prior.effective_to == date(2028, 1, 1)
    assert "regulatory_parameter.approve" in _actions(operator_db)


def test_propose_records_an_audit_row(
    operator_client: TestClient, operator_db: Session
) -> None:
    operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(effective_from="2027-03-01")
    )
    assert "regulatory_parameter.propose" in _actions(operator_db)


def test_duplicate_effective_from_is_conflict(operator_client: TestClient) -> None:
    # Two proposals at the same future generation date collide (409).
    first = operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(effective_from="2030-01-01")
    )
    assert first.status_code == 201, first.text
    dup = operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(effective_from="2030-01-01")
    )
    assert dup.status_code == 409


def test_back_dated_proposal_is_rejected(operator_client: TestClient) -> None:
    # Back-dating would rewrite what historical official runs resolved.
    resp = operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(effective_from="2020-01-01")
    )
    assert resp.status_code == 422
    assert "past" in resp.json()["error"]["message"].lower()


def test_negative_value_is_rejected(operator_client: TestClient) -> None:
    # A negative regulatory value would silently disable a prudential floor.
    resp = operator_client.post(
        BASE, headers=operator_headers(), json=_propose_body(value_numeric="-5")
    )
    assert resp.status_code == 422


def test_approve_unknown_parameter_is_404(operator_client: TestClient) -> None:
    resp = operator_client.post(
        f"{BASE}/{uuid.uuid4()}/approve", headers=operator_headers(), json={}
    )
    assert resp.status_code == 404
