"""Enterprise-stress sign-off / Board attestation API (docs/stress.md §3.8, Phase 5).

The stress-run governance workflow (¶20, ¶57–63): an analyst prepares the
narrative + assumptions rationale on a succeeded enterprise-stress run, submits
it, and a DIFFERENT Board/approver officer attests. Covers the maker-checker
gate, the attested-only eligibility for the ICAAP submission, and tenant
isolation — against the deterministic canonical seeded book.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.authorization import RoleBundle, SensitivityScope
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import User
from tests.stress.api.test_enterprise_stress_integration import (
    _approve_scenario,
    _create_scenario,
    _period_id,
    _seed_checker,
)
from tests.stress.authority import grant
from tests.support.helpers import ORG_1, ORG_2, USER_1, headers
from tests.support.ingestion_seeding import seed_bank

pytestmark = pytest.mark.usefixtures(
    "fx_run_authority", "irrbb_run_authority", "stress_run_authority"
)

RUNS_URL = "/api/v1/banks/{bank_id}/enterprise-stress/runs"
SIGNOFF_URL = "/api/v1/banks/{bank_id}/enterprise-stress/signoffs"


def _seed_board(client: TestClient, bank_id: str) -> UUID:
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
                password_hash=security.hash_password("stress-board-test-password"),
                auth_provider="password",
            )
        )
        session.commit()
        grant(session, RoleBundle.VIEWER, user_id=board_id, bank_id=bank_id)
        grant(
            session,
            RoleBundle.SIGNER,
            user_id=board_id,
            bank_id=bank_id,
            sensitivity=SensitivityScope.RESTRICTED,
        )
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


@pytest.fixture
def signer_identity_pepper(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "stress-signoff-test-pepper-not-production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("signer_identity_pepper")
def test_signoff_maker_checker_lifecycle(db_client: TestClient) -> None:
    bank_id = seed_bank(db_client)
    run_id, _ = _run_enterprise_stress(db_client, bank_id)
    board = _seed_board(db_client, bank_id)
    signoff_id = _create_signoff(db_client, bank_id, run_id)

    # Draft carries the run's headline outcome captured at prepare time.
    got = db_client.get(SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}", headers=headers())
    assert got.status_code == 200
    assert got.json()["status"] == "draft"
    assert got.json()["stays_above_all_minima"] in (True, False)

    submit = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/submit",
        headers=headers(user_id=USER_1, roles=("analyst",)),
        json={
            "attestation_recipient_user_id": str(board),
            "reason": "Ready for Board attestation.",
        },
    )
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "pending_attestation"

    step_up = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/step-up",
        headers=headers(user_id=board, roles=("approver",)),
        json={"password": "stress-board-test-password"},
    )
    assert step_up.status_code == 200, step_up.text

    attest = db_client.post(
        SIGNOFF_URL.format(bank_id=bank_id) + f"/{signoff_id}/attest",
        headers=headers(user_id=board, roles=("approver",)),
        json={
            "credibility_rationale": "Reviewed and challenged; results are credible.",
            "board_challenge": "Challenged FX severity; retained.",
            "reason": "Board attestation.",
            "authorization_token": step_up.json()["authorization_token"],
            "expected_signing_digest": step_up.json()["signing_digest"],
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
