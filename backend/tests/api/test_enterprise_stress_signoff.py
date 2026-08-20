"""Enterprise-stress sign-off / Board attestation API (docs/stress.md §3.8, Phase 5).

The stress-run governance workflow (¶20, ¶57–63): an analyst prepares the
narrative + assumptions rationale on a succeeded enterprise-stress run, submits
it, and a DIFFERENT Board/approver officer attests. Covers the maker-checker
gate, the attested-only eligibility for the ICAAP submission, and tenant
isolation — against the deterministic canonical seeded book.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.db.session import get_sessionmaker
from app.models import User
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.api.test_enterprise_stress import (
    _approve_scenario,
    _create_scenario,
    _period_id,
    _seed_checker,
)
from tests.api.test_ingestion import seed_bank

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
SIGNOFF_URL = "/api/v1/banks/{bank_id}/enterprise-stress/signoffs"


def _seed_board(client: TestClient) -> UUID:
    _ = client
    board_id = uuid4()
    session = get_sessionmaker()()
    try:
        session.add(
            User(
                id=board_id,
                organization_id=ORG_1,
                email=f"board-{board_id.hex[:8]}@aequoros.example",
                display_name="Board Member",
                role="approver",
            )
        )
        session.commit()
    finally:
        session.close()
    return board_id


def _run_enterprise_stress(client: TestClient, bank_id: str) -> tuple[str, str]:
    period_id = _period_id(client, bank_id)
    checker = _seed_checker(client)
    scenario_id = _create_scenario(client)
    _approve_scenario(client, scenario_id, checker)
    response = client.post(
        RUNS_URL.format(bank_id=bank_id),
        headers=headers(),
        json={
            "scenario_id": scenario_id,
            "reporting_period_id": period_id,
            "reason": "Annual ICAAP stress test.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"], period_id


def _create_signoff(client: TestClient, bank_id: str, run_id: str) -> str:
    response = client.post(
        SIGNOFF_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "run_id": run_id,
            "scenario_narrative": "Enterprise-wide adverse macro scenario.",
            "assumptions_rationale": "Documented elasticities; overlays challenged.",
            "methodology_summary": "Bottom-up credit + coherent fan-out.",
            "reason": "Prepare the stress-run sign-off.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_signoff_maker_checker_lifecycle(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    run_id, _ = _run_enterprise_stress(db_client, bank_id)
    board = _seed_board(db_client)
    signoff_id = _create_signoff(db_client, bank_id, run_id)

    # Draft carries the run's headline outcome captured at prepare time.
    got = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}", headers=headers()
    )
    assert got.status_code == 200
    assert got.json()["status"] == "draft"
    assert got.json()["stays_above_all_minima"] in (True, False)

    submit = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={"reason": "Ready for Board attestation."},
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "pending_attestation"

    # Maker ≠ checker: the preparer/submitter cannot attest their own sign-off.
    self_attest = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/attest",
        headers=headers(user_id=USER_1, roles=("approver",)),
        json={"credibility_rationale": "self", "reason": "self attest"},
    )
    assert self_attest.status_code == 409, self_attest.text
    assert self_attest.json()["error"]["details"]["error_code"] == "maker_is_checker"

    attest = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/attest",
        headers=headers(user_id=board, roles=("approver",)),
        json={
            "credibility_rationale": "Reviewed and challenged; results are credible.",
            "board_challenge": "Challenged FX severity; retained.",
            "reason": "Board attestation.",
        },
    )
    assert attest.status_code == 200, attest.text
    body = attest.json()
    assert body["status"] == "attested"
    assert body["attested_by"] == str(board)
    assert body["credibility_rationale"]
    assert body["version"] == 2


def test_signoff_requires_a_succeeded_run(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    _run_enterprise_stress(db_client, bank_id)  # ensures period/scenario exist
    missing = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id),
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "run_id": str(uuid4()),
            "scenario_narrative": "n",
            "assumptions_rationale": "r",
            "reason": "prepare",
        },
    )
    assert missing.status_code == 404, missing.text


def test_signoff_is_tenant_isolated(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    run_id, _ = _run_enterprise_stress(db_client, bank_id)
    signoff_id = _create_signoff(db_client, bank_id, run_id)
    foreign = db_client.get(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}", headers=headers(ORG_2)
    )
    assert foreign.status_code == 404
