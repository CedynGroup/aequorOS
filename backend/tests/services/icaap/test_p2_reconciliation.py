"""Reconciling capital, allocating it, and proposing a capital-plan update.

The proposal test is the load-bearing one: it proves the figures reach the
capital plan through the plan's own maker-checker rather than around it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.models import BankReportingPeriod, User
from app.schemas.icaap import IcaapCycleRead, IcaapDataBlockCreate
from app.schemas.icaap_risk_capital import (
    IcaapAllocationDriverPut,
    IcaapAllocationPut,
    IcaapAllocationUnitPut,
    IcaapCapitalPlanProposalCreate,
    IcaapExplanation,
    IcaapPillar2Approve,
    IcaapPillar2Compute,
    IcaapPillar2ItemCreate,
    IcaapPillar2ManualInputs,
    IcaapReason,
    IcaapResourcesLineCreate,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import capital_plan, regulatory_capital
from app.services.icaap import allocation, blocks, pillar2, reconciliation
from tests.api.helpers import ORG_1
from tests.services.icaap.conftest import AS_OF

CHECKER = uuid4()


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


@pytest.fixture
def checker(canonical_book: Session, access: IcaapAccess) -> IcaapAccess:
    canonical_book.add(
        User(
            id=CHECKER,
            organization_id=ORG_1,
            email="recon.checker@example.test",
            display_name="Second Person",
        )
    )
    canonical_book.commit()
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=CHECKER, authorization_version=1),
        bank=access.bank,
    )


@pytest.fixture
def bound(canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead) -> None:
    """A sealed capital run for the year end, with both capital blocks linked."""
    period = canonical_book.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None
    regulatory_capital.create_capital_run(
        canonical_book,
        access.ctx,
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    canonical_book.commit()
    for block_type in ("capital_position", "pillar1_rwa"):
        blocks.create_block(
            canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type=block_type)
        )


def _quantified_irrbb(
    db: Session, access: IcaapAccess, cycle: IcaapCycleRead, checker: IcaapAccess
):
    item = pillar2.create_item(
        db,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="irrbb",
            method="irrbb_interim_delta_eve",
            input_mode="manual_with_evidence",
            rationale="Group ALM figures.",
            reason="Quantify IRRBB.",
        ),
    )
    computed = pillar2.compute_item(
        db,
        access,
        cycle.id,
        item.id,
        IcaapPillar2Compute(
            base_revision_no=item.current_revision_no,
            manual_inputs=IcaapPillar2ManualInputs(
                tier1=Decimal("700"),
                irrbb_deltas={
                    "parallel_up_450": Decimal("-116.759902"),
                    "parallel_down_450": Decimal("142.996097"),
                },
            ),
            reason="Compute.",
        ),
    )
    return pillar2.approve_item(
        db,
        checker,
        cycle.id,
        item.id,
        IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Reviewed."),
    )


# --- requirement -----------------------------------------------------------


def test_without_a_linked_capital_position_the_requirement_cannot_be_reconciled(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        reconciliation.compute_requirement(
            canonical_book, access, cycle.id, IcaapReason(reason="Reconcile.")
        )
    body = _detail(caught)
    assert body["error_code"] == "denominator_missing"
    assert body["basis"] == "total_rwa"


def test_the_requirement_lines_up_pillar_one_pillar_two_and_the_buffers(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    read = reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="Reconcile the requirement.")
    )
    keys = {line.line_key for line in read.requirement.lines}
    assert {"pillar1_credit", "pillar1_market", "pillar1_operational"} <= keys
    assert any(key.startswith("pillar2_") for key in keys)
    assert read.requirement.totals.total_internal_requirement is not None
    assert read.requirement.stale is False
    used = {entry.param_code for entry in read.parameters}
    assert {"car_min", "ccb1_pct", "ccyb_pct", "dsib_buffer_pct"} <= used


def test_an_explanation_survives_a_recompute_and_says_when_it_is_out_of_date(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    bound: None,
    checker: IcaapAccess,
) -> None:
    reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="First pass.")
    )
    read = reconciliation.explain_requirement_line(
        canonical_book,
        access,
        cycle.id,
        "pillar2_irrbb",
        IcaapExplanation(
            explanation="Nothing is quantified for interest rate risk yet.",
            reason="Record the position.",
        ),
    )
    line = next(entry for entry in read.requirement.lines if entry.line_key == "pillar2_irrbb")
    assert line.explanation is not None
    assert line.explanation_current is True

    _quantified_irrbb(canonical_book, access, cycle, checker)
    after = reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="After quantifying IRRBB.")
    )
    line = next(entry for entry in after.requirement.lines if entry.line_key == "pillar2_irrbb")
    assert line.explanation is not None, "the explanation is carried, not dropped"
    assert line.explanation_current is False, "but it was written against older figures"
    assert line.internal_amount == Decimal("116.7599")


def test_a_recompute_is_needed_when_the_figures_behind_it_move(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    bound: None,
    checker: IcaapAccess,
) -> None:
    reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="First pass.")
    )
    _quantified_irrbb(canonical_book, access, cycle, checker)
    read = reconciliation.get_reconciliation(canonical_book, access, cycle.id)
    assert read.requirement.stale is True


# --- resources -------------------------------------------------------------


def test_a_component_the_rules_do_not_recognise_needs_an_explanation(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    with pytest.raises(HTTPException) as caught:
        reconciliation.create_resources_line(
            canonical_book,
            access,
            cycle.id,
            IcaapResourcesLineCreate(
                line_key="unaudited_profit",
                label="Unaudited current-year profit",
                tier="cet1",
                internal_amount=Decimal("10"),
                regulatory_amount=None,
                regulatory_eligible=False,
                reason="Count it internally.",
            ),
        )
    assert _detail(caught)["error_code"] == "explanation_required"


def test_recognition_caps_come_from_the_control_plane(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="Reconcile.")
    )
    read = reconciliation.create_resources_line(
        canonical_book,
        access,
        cycle.id,
        IcaapResourcesLineCreate(
            line_key="cet1_core",
            label="Common equity tier 1",
            tier="cet1",
            internal_amount=Decimal("250"),
            regulatory_amount=Decimal("250"),
            regulatory_eligible=True,
            reason="Load the core component.",
        ),
    )
    caps = {entry.param_code for entry in read.resources.caps}
    assert caps == set(reconciliation.CAP_CODES)
    assert read.resources.totals.available_internal_capital == Decimal("250.0000")
    assert read.resources.totals.internal_capital_coverage_pct is not None


def test_the_capital_runs_own_components_can_be_loaded_with_their_provenance(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    read = reconciliation.load_regulatory_components(
        canonical_book, access, cycle.id, IcaapReason(reason="Seed from the run.")
    )
    assert read.resources.lines, "the sealed run publishes capital components"
    loaded = read.resources.lines[0]
    assert loaded.origin == "regulatory_component"
    assert loaded.source_binding_ref is not None
    assert "payload_sha256" in loaded.source_binding_ref


# --- allocation ------------------------------------------------------------


def test_allocation_waits_for_the_requirement(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    read = allocation.get_allocation(canonical_book, access, cycle.id)
    assert read.available is False
    assert read.unavailable_reason is not None


def test_the_allocated_parts_add_back_to_the_line_exactly(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    computed = reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="Reconcile.")
    )
    line = next(
        entry
        for entry in computed.requirement.lines
        if entry.line_key == "pillar1_credit" and entry.internal_amount
    )
    read = allocation.put_allocation(
        canonical_book,
        access,
        cycle.id,
        IcaapAllocationPut(
            units=[
                IcaapAllocationUnitPut(
                    unit_key="retail", unit_label="Retail", unit_kind="business_line"
                ),
                IcaapAllocationUnitPut(
                    unit_key="corporate", unit_label="Corporate", unit_kind="business_line"
                ),
                IcaapAllocationUnitPut(
                    unit_key="treasury", unit_label="Treasury", unit_kind="business_line"
                ),
            ],
            drivers=[
                IcaapAllocationDriverPut(
                    unit_key=unit,
                    risk_line_key="pillar1_credit",
                    driver_kind="rwa_share",
                    driver_value=Decimal("1"),
                )
                for unit in ("retail", "corporate", "treasury")
            ],
            reason="Allocate credit risk across the business lines.",
        ),
    )
    allocated = [cell.allocated_amount for cell in read.cells if cell.allocated_amount is not None]
    assert len(allocated) == 3
    assert sum(allocated) == line.internal_amount


def test_a_driver_naming_a_line_this_icaap_does_not_have_is_refused(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    reconciliation.compute_requirement(
        canonical_book, access, cycle.id, IcaapReason(reason="Reconcile.")
    )
    with pytest.raises(HTTPException) as caught:
        allocation.put_allocation(
            canonical_book,
            access,
            cycle.id,
            IcaapAllocationPut(
                units=[
                    IcaapAllocationUnitPut(
                        unit_key="retail", unit_label="Retail", unit_kind="business_line"
                    )
                ],
                drivers=[
                    IcaapAllocationDriverPut(
                        unit_key="retail",
                        risk_line_key="invented_line",
                        driver_kind="rwa_share",
                        driver_value=Decimal("1"),
                    )
                ],
                reason="Allocate.",
            ),
        )
    assert _detail(caught)["error_code"] == "unknown_risk_key"


# --- capital-plan proposal -------------------------------------------------


def test_the_proposal_writes_a_draft_through_the_plans_own_maker_checker(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    bound: None,
    checker: IcaapAccess,
) -> None:
    """The proposer becomes the preparer, so a different person must approve."""
    _quantified_irrbb(canonical_book, access, cycle, checker)
    proposal = pillar2.propose_capital_plan_update(
        canonical_book,
        access,
        cycle.id,
        IcaapCapitalPlanProposalCreate(reason="Carry the ICAAP figures into the plan."),
    )
    assert proposal.status == "draft"
    assert [entry.item_key for entry in proposal.addons] == ["irrbb"]
    carried = proposal.addons[0]
    assert carried.amount == Decimal("116.7599")
    assert carried.add_on_pct_rwa > Decimal("0")
    assert "ICAAP FY2025 (solo)" in carried.rationale

    plan = capital_plan.get_capital_plan(canonical_book, access.ctx, access.bank.id)
    assert plan.current is not None
    assert plan.current.status == "draft"
    assert plan.current.prepared_by == access.ctx.actor_user_id

    with pytest.raises(HTTPException) as caught:
        capital_plan.approve_capital_plan(
            canonical_book,
            access.ctx,
            access.bank.id,
            _approve("The preparer tries to approve their own draft."),
        )
    assert _detail(caught)["error_code"] == "self_approval"


def _approve(reason: str):
    from app.schemas.capital_plan import CapitalPlanApprove  # noqa: PLC0415

    return CapitalPlanApprove.model_validate(
        {"approval_reference": "Board minute 2025/12", "reason": reason}
    )


def test_a_proposal_with_nothing_approved_refuses_and_names_what_is_missing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound: None
) -> None:
    pillar2.create_item(
        canonical_book,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="irrbb",
            method="irrbb_interim_delta_eve",
            input_mode="manual_with_evidence",
            rationale="Group ALM figures.",
            reason="Quantify IRRBB.",
        ),
    )
    with pytest.raises(HTTPException) as caught:
        pillar2.propose_capital_plan_update(
            canonical_book,
            access,
            cycle.id,
            IcaapCapitalPlanProposalCreate(reason="Too early."),
        )
    body = _detail(caught)
    assert body["error_code"] == "proposal_items_not_ready"
    assert body["items"] == ["irrbb"]
