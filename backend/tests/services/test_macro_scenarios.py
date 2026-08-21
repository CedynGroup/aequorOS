"""Service-level macro-scenario tests (docs/stress.md §3.1, Phase 1).

The official-run consumption guard (approved-only) and the supervisory
(``bank_id`` NULL) code-uniqueness path — both easier to exercise against the
service directly than through the API.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import User
from app.schemas.stress import (
    MacroScenarioApproval,
    MacroScenarioCreate,
    MacroScenarioTransition,
)
from app.services import macro_scenarios
from tests.api.helpers import ORG_1, USER_1

CHECKER = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
MAKER_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
CHECKER_CTX = TenantContext(organization_id=ORG_1, actor_user_id=CHECKER)


def _seed_checker(db_session: Session) -> None:
    """A second ORG_1 user so the approval audit event's actor FK resolves."""
    db_session.add(
        User(
            id=CHECKER,
            organization_id=ORG_1,
            email="scenario-checker@aequoros.example",
            display_name="Scenario Checker",
            role="approver",
        )
    )
    db_session.commit()


def _supervisory_payload(code: str = "bog_supervisory_2027") -> MacroScenarioCreate:
    return MacroScenarioCreate.model_validate(
        {
            "code": code,
            "name": "BoG supervisory downturn",
            "scenario_type": "supervisory",
            "severity": "severe",
            "horizon_years": 3,
            "bank_id": None,
            "paths": [
                {
                    "variable": "gdp_growth",
                    "year_index": 1,
                    "base_value": "0.05",
                    "stress_value": "-0.02",
                }
            ],
            "reason": "Load the BoG bottom-up supervisory scenario.",
        }
    )


def test_official_run_guard_refuses_until_approved(db_session: Session) -> None:
    _seed_checker(db_session)
    created = macro_scenarios.create_scenario(db_session, MAKER_CTX, _supervisory_payload())
    assert created.bank_id is None  # supervisory, org-wide

    # Draft: refused.
    with pytest.raises(HTTPException) as draft_exc:
        macro_scenarios.resolve_for_official_run(db_session, MAKER_CTX, created.id)
    assert draft_exc.value.status_code == 409
    assert draft_exc.value.detail["error_code"] == "scenario_not_approved"  # type: ignore[index]

    macro_scenarios.submit_scenario(
        db_session, MAKER_CTX, created.id, MacroScenarioTransition(reason="submit")
    )
    # Pending: still refused.
    with pytest.raises(HTTPException):
        macro_scenarios.resolve_for_official_run(db_session, MAKER_CTX, created.id)

    macro_scenarios.approve_scenario(
        db_session, CHECKER_CTX, created.id, MacroScenarioApproval(reason="approve")
    )
    # Approved: resolves to the scenario.
    resolved = macro_scenarios.resolve_for_official_run(db_session, MAKER_CTX, created.id)
    assert resolved.id == created.id
    assert resolved.status == "approved"


def test_supervisory_code_uniqueness_enforced_for_null_bank(db_session: Session) -> None:
    macro_scenarios.create_scenario(db_session, MAKER_CTX, _supervisory_payload())
    with pytest.raises(HTTPException) as exc:
        macro_scenarios.create_scenario(db_session, MAKER_CTX, _supervisory_payload())
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "scenario_code_exists"  # type: ignore[index]
