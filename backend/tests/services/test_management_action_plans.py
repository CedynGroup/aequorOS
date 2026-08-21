"""Service-level management-action plan tests (docs/stress.md §3.7, Phase 3).

The official-run consumption guard (approved-only), maker ≠ checker, and the pure
``build_domain_plan`` / ``plan_snapshot`` conversions — easier to exercise against
the service directly than through the API.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import User
from app.schemas.management_actions import (
    ManagementActionPlanApproval,
    ManagementActionPlanCreate,
    ManagementActionPlanTransition,
)
from app.services import management_action_plans
from tests.api.helpers import ORG_1, USER_1

CHECKER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
MAKER_CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
CHECKER_CTX = TenantContext(organization_id=ORG_1, actor_user_id=CHECKER)


def _seed_checker(db_session: Session) -> None:
    db_session.add(
        User(
            id=CHECKER,
            organization_id=ORG_1,
            email="plan-checker@aequoros.example",
            display_name="Plan Checker",
            role="approver",
        )
    )
    db_session.commit()


def _payload(code: str = "recovery_2027") -> ManagementActionPlanCreate:
    return ManagementActionPlanCreate.model_validate(
        {
            "code": code,
            "name": "Capital-restoration plan",
            "bank_id": None,
            "actions": [
                {
                    "action_id": "dividend_suspension",
                    "kind": "revise_dividend",
                    "label": "Suspend dividends",
                    "trigger_kind": "on_breach",
                    "effective_year": 1,
                    "dividend_reduction_pct": "100",
                },
                {
                    "action_id": "capital_raise",
                    "kind": "raise_capital",
                    "label": "Equity issuance",
                    "trigger_kind": "on_breach",
                    "watch_minima": ["car", "paid_up"],
                    "effective_year": 2,
                    "sizing": "fill_residual",
                    "capital_raise_ghs": "1",
                    "counts_as_paid_up": True,
                    "severity_factors": {"mild": "0.4", "moderate": "0.7", "severe": "1.0"},
                },
            ],
            "reason": "Author the recovery plan.",
        }
    )


def test_official_run_guard_refuses_until_approved(db_session: Session) -> None:
    _seed_checker(db_session)
    created = management_action_plans.create_plan(db_session, MAKER_CTX, _payload())
    assert created.bank_id is None  # org-wide default plan

    with pytest.raises(HTTPException) as draft_exc:
        management_action_plans.resolve_for_official_run(db_session, MAKER_CTX, created.id)
    assert draft_exc.value.status_code == 409
    assert draft_exc.value.detail["error_code"] == "plan_not_approved"  # type: ignore[index]

    management_action_plans.submit_plan(
        db_session, MAKER_CTX, created.id, ManagementActionPlanTransition(reason="submit")
    )
    # Maker ≠ checker: the creator cannot approve.
    with pytest.raises(HTTPException) as maker_exc:
        management_action_plans.approve_plan(
            db_session, MAKER_CTX, created.id, ManagementActionPlanApproval(reason="self")
        )
    assert maker_exc.value.detail["error_code"] == "maker_is_checker"  # type: ignore[index]

    management_action_plans.approve_plan(
        db_session, CHECKER_CTX, created.id, ManagementActionPlanApproval(reason="approve")
    )
    resolved = management_action_plans.resolve_for_official_run(db_session, MAKER_CTX, created.id)
    assert resolved.id == created.id
    assert resolved.status == "approved"


def test_build_domain_plan_round_trips_the_action_contract(db_session: Session) -> None:
    _seed_checker(db_session)
    created = management_action_plans.create_plan(db_session, MAKER_CTX, _payload())
    plan_model = management_action_plans._get_plan_or_404(db_session, MAKER_CTX, created.id)
    domain = management_action_plans.build_domain_plan(db_session, plan_model)
    assert domain.plan_id == "recovery_2027"
    by_id = {action.action_id: action for action in domain.actions}
    raise_action = by_id["capital_raise"]
    assert raise_action.kind == "raise_capital"
    assert raise_action.sizing == "fill_residual"
    assert raise_action.effective_year == 2
    assert raise_action.counts_as_paid_up is True
    assert raise_action.trigger.kind == "on_breach"
    assert raise_action.trigger.watch_minima == ("car", "paid_up")
    assert raise_action.severity_factors["severe"] == Decimal("1.0")
    dividend = by_id["dividend_suspension"]
    assert dividend.dividend_reduction_pct == Decimal("100")


def test_plan_snapshot_is_value_based_and_stable(db_session: Session) -> None:
    _seed_checker(db_session)
    created = management_action_plans.create_plan(db_session, MAKER_CTX, _payload())
    plan_model = management_action_plans._get_plan_or_404(db_session, MAKER_CTX, created.id)
    first = management_action_plans.plan_snapshot(db_session, plan_model)
    second = management_action_plans.plan_snapshot(db_session, plan_model)
    assert first == second
    assert first["code"] == "recovery_2027"
    action_ids = [action["action_id"] for action in first["actions"]]
    assert action_ids == sorted(action_ids)  # canonically ordered


def test_duplicate_code_is_refused(db_session: Session) -> None:
    _seed_checker(db_session)
    management_action_plans.create_plan(db_session, MAKER_CTX, _payload())
    with pytest.raises(HTTPException) as exc:
        management_action_plans.create_plan(db_session, MAKER_CTX, _payload())
    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "plan_code_exists"  # type: ignore[index]
