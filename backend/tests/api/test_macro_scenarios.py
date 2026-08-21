"""Governed macro-scenario library API (docs/stress.md §3.1, Phase 1).

Covers the CRUD + maker-checker lifecycle (draft-only edits, submit → approve
with maker ≠ checker enforced, approved immutability, archive), validation
rejects, the translation preview seam, and tenant isolation.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import User
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.api.test_ingestion import seed_bank

URL = "/api/v1/macro-scenarios"

# A distinct maker/checker pair within ORG_1 so maker ≠ checker can be exercised.
MAKER = USER_1


def _path(variable: str, year_index: int, base: str, stress: str) -> dict:
    return {
        "variable": variable,
        "year_index": year_index,
        "base_value": base,
        "stress_value": stress,
    }


def _seed_checker(client: TestClient) -> UUID:
    """A second ACTIVE ORG_1 user so maker-checker has a distinct approver."""
    _ = client  # engine already initialised by the fixture
    checker_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=checker_id,
                organization_id=ORG_1,
                email=f"checker-{checker_id.hex[:8]}@aequoros.example",
                display_name="Scenario Checker",
                role="approver",
            )
        )
        session.commit()
    finally:
        session.close()
    return checker_id


def _create_payload(code: str = "adverse_2027", bank_id: str | None = None) -> dict:
    return {
        "code": code,
        "name": "2027 severe downturn",
        "description": "Severe but plausible domestic downturn.",
        "scenario_type": "adverse",
        "severity": "severe",
        "horizon_years": 3,
        "narrative": "GDP contraction, cedi depreciation, rate spike.",
        "source": "BoG MPC + internal desk",
        "bank_id": bank_id,
        "paths": [
            _path("interest_rate", 1, "0.20", "0.25"),
            _path("gdp_growth", 1, "0.05", "0.00"),
            _path("fx_usd_ghs", 1, "12.5", "15.0"),
        ],
        "reason": "Author the annual adverse scenario.",
    }


def _create(client: TestClient, **kwargs) -> dict:
    response = client.post(
        URL, headers=headers(user_id=MAKER, roles=("analyst",)), json=_create_payload(**kwargs)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _detail(response) -> dict:
    return response.json()["error"]["details"]


def test_create_lists_and_gets_a_draft(db_client: TestClient) -> None:
    created = _create(db_client)
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert created["created_by"] == str(MAKER)
    assert created["approved_by"] is None
    assert len(created["paths"]) == 3

    listing = db_client.get(URL, headers=headers()).json()
    assert listing["total"] == 1
    assert listing["scenarios"][0]["path_count"] == 3
    assert listing["scenarios"][0]["status"] == "draft"

    fetched = db_client.get(f"{URL}/{created['id']}", headers=headers()).json()
    assert fetched["code"] == "adverse_2027"


def test_duplicate_code_rejected(db_client: TestClient) -> None:
    _create(db_client)
    dup = db_client.post(
        URL, headers=headers(user_id=MAKER, roles=("analyst",)), json=_create_payload()
    )
    assert dup.status_code == 409
    assert _detail(dup)["error_code"] == "scenario_code_exists"


def test_full_lifecycle_maker_checker(db_client: TestClient) -> None:
    checker = _seed_checker(db_client)
    created = _create(db_client)
    scenario_id = created["id"]

    # Submit for approval (maker).
    submit = db_client.post(
        f"{URL}/{scenario_id}/submit",
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json={"reason": "Ready for Stress Testing Committee review."},
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "pending_approval"

    # The maker cannot approve their own scenario (maker ≠ checker).
    self_approve = db_client.post(
        f"{URL}/{scenario_id}/approve",
        headers=headers(user_id=MAKER, roles=("approver",)),
        json={"reason": "self approval attempt"},
    )
    assert self_approve.status_code == 409
    assert _detail(self_approve)["error_code"] == "maker_is_checker"

    # A different approver approves — status/approved_by/timestamp set, version bumped.
    approve = db_client.post(
        f"{URL}/{scenario_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "Committee approved on 2026-08-19."},
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == str(checker)
    assert body["approval_timestamp"] is not None
    assert body["version"] == 2


def test_approved_scenario_is_immutable(db_client: TestClient) -> None:
    checker = _seed_checker(db_client)
    created = _create(db_client)
    scenario_id = created["id"]
    db_client.post(
        f"{URL}/{scenario_id}/submit",
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json={"reason": "submit"},
    )
    db_client.post(
        f"{URL}/{scenario_id}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "approve"},
    )
    edit = db_client.patch(
        f"{URL}/{scenario_id}",
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json={"name": "renamed", "reason": "edit after approval"},
    )
    assert edit.status_code == 409
    assert _detail(edit)["error_code"] == "not_editable"


def test_draft_edit_replaces_paths(db_client: TestClient) -> None:
    created = _create(db_client)
    scenario_id = created["id"]
    edit = db_client.patch(
        f"{URL}/{scenario_id}",
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json={
            "name": "revised draft",
            "paths": [_path("inflation", 2, "0.15", "0.25")],
            "reason": "Tighten the inflation path.",
        },
    )
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert body["name"] == "revised draft"
    assert len(body["paths"]) == 1
    assert body["paths"][0]["variable"] == "inflation"


def test_cannot_approve_a_draft(db_client: TestClient) -> None:
    checker = _seed_checker(db_client)
    created = _create(db_client)
    approve = db_client.post(
        f"{URL}/{created['id']}/approve",
        headers=headers(user_id=checker, roles=("approver",)),
        json={"reason": "skip submit"},
    )
    assert approve.status_code == 409
    assert _detail(approve)["error_code"] == "not_pending_approval"


def test_archive_hides_from_default_listing(db_client: TestClient) -> None:
    created = _create(db_client)
    archive = db_client.post(
        f"{URL}/{created['id']}/archive",
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json={"reason": "Superseded by the 2028 scenario."},
    )
    assert archive.status_code == 200
    assert archive.json()["status"] == "archived"
    assert db_client.get(URL, headers=headers()).json()["total"] == 0
    with_archived = db_client.get(f"{URL}?include_archived=true", headers=headers()).json()
    assert with_archived["total"] == 1


def test_translation_preview_endpoint(db_client: TestClient) -> None:
    created = _create(db_client)
    irr = db_client.get(
        f"{URL}/{created['id']}/translation/irr", headers=headers()
    ).json()
    assert irr["module"] == "irr"
    # interest_rate Δ +0.05 → 500bp parallel shift.
    assert Decimal(irr["shocks"]["parallel_bp"]) == Decimal("500")

    liquidity = db_client.get(
        f"{URL}/{created['id']}/translation/liquidity", headers=headers()
    ).json()["shocks"]
    assert Decimal(liquidity["fx_depreciation_pct"]) == Decimal("20")
    assert Decimal(liquidity["inflow_multiplier"]) == Decimal("0.8")


def test_bank_scoped_scenario_validates_bank(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    ok = _create(db_client, code="bank_scoped", bank_id=bank_id)
    assert ok["bank_id"] == bank_id
    bad = db_client.post(
        URL,
        headers=headers(user_id=MAKER, roles=("analyst",)),
        json=_create_payload(code="bad_bank", bank_id="BK-DOESNOT1"),
    )
    assert bad.status_code == 404


def test_validation_rejects(db_client: TestClient) -> None:
    # Unknown macro variable.
    bad_var = _create_payload(code="bad_var")
    bad_var["paths"] = [
        {"variable": "house_prices", "year_index": 1, "base_value": "1", "stress_value": "2"}
    ]
    r1 = db_client.post(URL, headers=headers(roles=("analyst",)), json=bad_var)
    assert r1.status_code == 422

    # year_index beyond the horizon.
    over = _create_payload(code="over_horizon")
    over["horizon_years"] = 2
    over["paths"] = [
        {"variable": "inflation", "year_index": 5, "base_value": "0.1", "stress_value": "0.2"}
    ]
    r2 = db_client.post(URL, headers=headers(roles=("analyst",)), json=over)
    assert r2.status_code == 422

    # Empty path set.
    empty = _create_payload(code="empty")
    empty["paths"] = []
    r3 = db_client.post(URL, headers=headers(roles=("analyst",)), json=empty)
    assert r3.status_code == 422


def test_rbac_and_tenant_isolation(db_client: TestClient) -> None:
    # Viewer cannot create.
    viewer = db_client.post(
        URL, headers=headers(roles=("viewer",)), json=_create_payload(code="viewer_try")
    )
    assert viewer.status_code == 403

    created = _create(db_client)
    # A different org cannot see or fetch this scenario (org-scoped queries).
    foreign_list = db_client.get(URL, headers=headers(ORG_2)).json()
    assert foreign_list["total"] == 0
    foreign_get = db_client.get(f"{URL}/{created['id']}", headers=headers(ORG_2))
    assert foreign_get.status_code == 404
