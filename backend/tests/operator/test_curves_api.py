"""The forward-curve construction console API (FC-3): auth gate, governance, preview.

Definitions are Track-2 governed at the API boundary (dual control, the operator's
own email is the actor); construction is an approved-definition-only preview; every
mutation lands an ``operator_audit_log`` row. Heavy publish fan-out landing is proven
at the service layer (``tests/services/test_curve_construction.py``); here publish is
a wiring smoke test (no banks seeded -> a complete, empty fan-out).
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeskDetermination, OperatorAuditLog
from app.services.market_desk import curve_construction as cc
from tests.operator.conftest import operator_headers

BASE = "/operator/v1/curves"
DESK = "/operator/v1/desk"
DEV_EMAIL = "dev@aequoros.com"
ANALYST = "analyst@aequoros.com"
CURVE_CODE = "AEQ.GHS.SOV.FWD"
AS_OF = "2026-08-07"

QUOTES = [
    {"instrument": "deposit", "tenor": tenor, "quote": quote}
    for tenor, quote in [("3M", 0.245), ("6M", 0.249), ("1Y", 0.254), ("2Y", 0.258), ("3Y", 0.262)]
]


def _definition_body(curve_code: str = CURVE_CODE, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "curve_code": curve_code,
        "currency": "GHS",
        "calendar_name": "GHANA",
        "curve_kind": "forward",
        "projection_index": "GHS_SOVEREIGN",
        "instrument_set_ref": "GHS - Sovereign bills (money-market proxy)",
        "interpolation_method": "monotone_convex",
        "output_daycount": "ACT_360",
        "payment_interval_months": 3,
        "curve_frequency": "3M",
        "params": {"grid_periods": 8, "pillar_min_count": 3},
        "change_rationale": "initial AEQ.GHS.SOV.FWD definition",
    }
    body.update(overrides)
    return body


def _spec(curve_code: str = CURVE_CODE) -> cc.CurveDefinitionSpec:
    return cc.CurveDefinitionSpec(
        curve_code=curve_code,
        currency="GHS",
        calendar_name="GHANA",
        curve_kind="forward",
        projection_index="GHS_SOVEREIGN",
        discount_curve_code=None,
        instrument_set_ref="GHS - Sovereign bills (money-market proxy)",
        interpolation_method="monotone_convex",
        output_daycount="ACT_360",
        payment_frequency="quarterly",
        payment_interval_months=3,
        curve_frequency="3M",
        spot_lag_days=2,
        roll_convention="modified_following",
        extrapolation_rule="flat_forward",
        params={"grid_periods": 8, "pillar_min_count": 3},
    )


def _seed_definition(db: Session, *, approve: bool) -> None:
    """Seed a definition with a distinct proposer so the dev operator can approve it."""
    cc.create_definition(
        db, spec=_spec(), change_rationale="seed", proposed_by=ANALYST
    )
    if approve:
        cc.approve_definition_version(
            db,
            curve_code=CURVE_CODE,
            version=1,
            approved_by=DEV_EMAIL,
            effective_from=date(2026, 1, 1),
        )
    db.commit()


def _bootstrap_methodology(client: TestClient) -> None:
    seeded = client.post(f"{DESK}/methodologies/ensure-default", headers=operator_headers())
    assert seeded.status_code == 201, seeded.text
    approved = client.post(
        f"{DESK}/methodologies/AEQ-GHS-CURVES/versions/1/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-01-01"},
    )
    assert approved.status_code == 200, approved.text


def _audit_actions(db: Session) -> list[str]:
    return list(db.scalars(select(OperatorAuditLog.action)))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_curve_endpoints_require_operator_context(operator_client: TestClient) -> None:
    assert operator_client.get(f"{BASE}/definitions").status_code == 401
    assert operator_client.post(f"{BASE}/definitions", json=_definition_body()).status_code == 401
    assert (
        operator_client.post(
            f"{BASE}/construct", json={"curve_code": CURVE_CODE, "as_of": AS_OF, "quotes": QUOTES}
        ).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# Definition governance
# ---------------------------------------------------------------------------


def test_create_and_list_definition(operator_client: TestClient, operator_db: Session) -> None:
    created = operator_client.post(
        f"{BASE}/definitions", headers=operator_headers(), json=_definition_body()
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["proposed_by"] == DEV_EMAIL

    listed = operator_client.get(f"{BASE}/definitions", headers=operator_headers())
    assert listed.status_code == 200
    assert [row["curve_code"] for row in listed.json()["definitions"]] == [CURVE_CODE]
    assert "desk.curve.create_definition" in _audit_actions(operator_db)


def test_self_approval_refused_at_api_boundary(operator_client: TestClient) -> None:
    created = operator_client.post(
        f"{BASE}/definitions", headers=operator_headers(), json=_definition_body()
    )
    assert created.status_code == 201, created.text
    # The dev operator proposed it; the same operator cannot approve (dual control).
    refused = operator_client.post(
        f"{BASE}/definitions/{CURVE_CODE}/versions/1/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-01-01"},
    )
    assert refused.status_code == 422, refused.text


def test_approve_and_construct(operator_client: TestClient, operator_db: Session) -> None:
    _seed_definition(operator_db, approve=False)

    approved = operator_client.post(
        f"{BASE}/definitions/{CURVE_CODE}/versions/1/approve",
        headers=operator_headers(),
        json={"effective_from": "2026-01-01"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    constructed = operator_client.post(
        f"{BASE}/construct",
        headers=operator_headers(),
        json={"curve_code": CURVE_CODE, "as_of": AS_OF, "quotes": QUOTES},
    )
    assert constructed.status_code == 200, constructed.text
    body = constructed.json()
    assert body["qa"]["passed"] is True
    assert body["qa"]["reprice_pass"] is True
    assert len(body["rows"]) == 9  # spot stub + 8 forward periods
    assert body["rows"][0]["discount_factor"] == 1.0
    assert body["rows"][0]["forward_yield"] == 0.0
    assert len(body["input_digest"]) == 64
    assert len(body["pillars"]) == len(QUOTES)

    actions = _audit_actions(operator_db)
    assert "desk.curve.approve_version" in actions
    assert "desk.curve.construct" in actions


def test_construct_requires_active_definition(operator_client: TestClient) -> None:
    # No definition approved for this code/date.
    response = operator_client.post(
        f"{BASE}/construct",
        headers=operator_headers(),
        json={"curve_code": CURVE_CODE, "as_of": AS_OF, "quotes": QUOTES},
    )
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Entitlement tier (FC-6d) on the definition CRUD
# ---------------------------------------------------------------------------


def test_definition_create_carries_entitlement_tier(operator_client: TestClient) -> None:
    created = operator_client.post(
        f"{BASE}/definitions", headers=operator_headers(), json=_definition_body()
    )
    assert created.status_code == 201, created.text
    assert created.json()["entitlement_tier"] == "standard"  # default

    premium = operator_client.post(
        f"{BASE}/definitions",
        headers=operator_headers(),
        json=_definition_body(curve_code="AEQ.GHS.CORP", entitlement_tier="premium"),
    )
    assert premium.status_code == 201, premium.text
    assert premium.json()["entitlement_tier"] == "premium"


# ---------------------------------------------------------------------------
# Per-cob maker-checker lifecycle (FC-G2)
# ---------------------------------------------------------------------------


def _stage_via_service(operator_db: Session, *, prepared_by: str) -> DeskDetermination:
    definition = cc.get_active_definition(operator_db, CURVE_CODE, date(2026, 8, 7))
    assert definition is not None
    row = cc.stage_curve_determination(
        operator_db,
        definition=definition,
        as_of=date(2026, 8, 7),
        quotes=QUOTES,
        calendar=cc.resolve_calendar(definition.calendar_name),
        prepared_by=prepared_by,
    )
    operator_db.commit()
    return row


def test_curve_lifecycle_submit_approve_publish(
    operator_client: TestClient, operator_db: Session
) -> None:
    """Wiring smoke: a draft (prepared by a distinct analyst) submits, is approved
    by the dev operator, and publishes through the determination seam."""
    _bootstrap_methodology(operator_client)
    _seed_definition(operator_db, approve=True)
    draft = _stage_via_service(operator_db, prepared_by=ANALYST)

    submitted = operator_client.post(
        f"{BASE}/determinations/{draft.id}/submit", headers=operator_headers()
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "pending_review"

    approved = operator_client.post(
        f"{BASE}/determinations/{draft.id}/approve", headers=operator_headers()
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    published = operator_client.post(
        f"{BASE}/determinations/{draft.id}/publish", headers=operator_headers()
    )
    assert published.status_code == 200, published.text
    body = published.json()
    # No banks seeded in the operator fixture: the fan-out is complete and empty.
    assert body["status"] == "complete"
    assert body["results"] == []

    determination = operator_db.scalar(
        select(DeskDetermination).where(DeskDetermination.status == "published")
    )
    assert determination is not None
    assert determination.derived_values["curves_qa_passed"] is True
    assert CURVE_CODE in determination.derived_values["curves"]
    actions = _audit_actions(operator_db)
    assert "desk.curve.submit" in actions
    assert "desk.curve.approve" in actions
    assert "desk.curve.publish" in actions


def test_curve_stage_then_self_approval_refused(
    operator_client: TestClient, operator_db: Session
) -> None:
    """The dev operator stages a draft via the API, then cannot approve it (422)."""
    _bootstrap_methodology(operator_client)
    _seed_definition(operator_db, approve=True)

    staged = operator_client.post(
        f"{BASE}/determinations",
        headers=operator_headers(),
        json={"curve_code": CURVE_CODE, "as_of": AS_OF, "quotes": QUOTES},
    )
    assert staged.status_code == 201, staged.text
    body = staged.json()
    assert body["status"] == "draft"
    assert body["prepared_by"] == DEV_EMAIL
    det_id = body["id"]
    assert "desk.curve.stage_draft" in _audit_actions(operator_db)

    submitted = operator_client.post(
        f"{BASE}/determinations/{det_id}/submit", headers=operator_headers()
    )
    assert submitted.status_code == 200, submitted.text

    refused = operator_client.post(
        f"{BASE}/determinations/{det_id}/approve", headers=operator_headers()
    )
    assert refused.status_code == 422, refused.text
