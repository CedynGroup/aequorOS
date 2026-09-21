"""The Pillar 2 register: computing a figure, approving it, and refusing without one.

The consolidated/manual path is used for most of these, because it lets the
test state the bank figures itself and assert the engine's published reference
case exactly (P2-DESIGN §2.11 IRRBB) rather than whatever the canonical book
happens to produce.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.domain.icaap.pillar2 import DEFERRED_METHODS
from app.domain.icaap.pillar2 import sovereign as sovereign_domain
from app.models import RegulatoryParameter, User
from app.models.icaap_risk_capital import IcaapPillar2ItemRevision
from app.schemas.icaap import IcaapCycleRead
from app.schemas.icaap_risk_capital import (
    IcaapPillar2Approve,
    IcaapPillar2Compute,
    IcaapPillar2ItemCreate,
    IcaapPillar2ItemUpdate,
    IcaapPillar2ManualInputs,
    IcaapRetire,
)
from app.services.icaap import params, pillar2
from tests.api.helpers import ORG_1
from tests.domain.icaap.pillar2.conftest import seed_body

#: A second person in the same tenant: the checker in every maker-checker test.
CHECKER = uuid4()

#: The §2.11 IRRBB reference case: the engine's signed deltas for four shocks.
DELTAS = {
    "parallel_up_450": Decimal("-116.759902"),
    "parallel_down_450": Decimal("142.996097"),
    "parallel_up_200": Decimal("-54.823916"),
    "parallel_down_200": Decimal("59.992189"),
}


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


@pytest.fixture
def checker(canonical_book: Session, access: IcaapAccess) -> IcaapAccess:
    """The same institution, a different person — the checker."""
    canonical_book.add(
        User(
            id=CHECKER,
            organization_id=ORG_1,
            email="checker@example.test",
            display_name="Second Person",
        )
    )
    canonical_book.commit()
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=CHECKER, authorization_version=1),
        bank=access.bank,
    )


def _create_irrbb(db: Session, access: IcaapAccess, cycle: IcaapCycleRead):
    return pillar2.create_item(
        db,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="irrbb",
            method="irrbb_interim_delta_eve",
            input_mode="manual_with_evidence",
            rationale="Consolidated figures supplied from the group ALM system.",
            reason="Quantify interest rate risk in the banking book.",
        ),
    )


def _compute_irrbb(  # noqa: PLR0913 - the addressed item plus the manual inputs
    db: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    item_id,
    revision: int,
    *,
    tier1: str = "700",
):
    return pillar2.compute_item(
        db,
        access,
        cycle.id,
        item_id,
        IcaapPillar2Compute(
            base_revision_no=revision,
            manual_inputs=IcaapPillar2ManualInputs(tier1=Decimal(tier1), irrbb_deltas=dict(DELTAS)),
            reason="Compute from the group ALM figures.",
        ),
    )


def test_a_new_cycle_offers_every_framework_component_and_holds_no_figures(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    register = pillar2.get_register(canonical_book, access, cycle.id)
    assert register.items == []
    slots = {slot.component_key for slot in register.components}
    assert {"credit_concentration", "irrbb", "fx", "liquidity"} <= slots
    irrbb = next(slot for slot in register.components if slot.component_key == "irrbb")
    # Both IRRBB methods are offered now: the interim one for reporting dates
    # before the framework commences, the framework itself from that date on.
    assert irrbb.deferred_methods == []
    assert {"irrbb_interim_delta_eve", "irrbb_standardised_framework"} <= set(
        irrbb.allowed_methods
    )
    assert all(row.baseline is None for row in register.table5_totals.rows)


def test_computing_reproduces_the_published_reference_case(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    assert computed.method_status == "interim_non_sf", "the platform labels it interim"
    assert computed.baseline_amount == Decimal("116.7599")
    assert computed.basis == "absolute"
    detail = (computed.computation or {})["detail"]
    assert detail["irrbb_outlier_measure_pct"] == "16.679986"
    assert detail["outlier"] == "true"
    assert computed.inputs_digest is not None


def test_the_outlier_verdict_follows_the_governed_threshold_not_the_code(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """Tier 1 of 800 puts the same loss under the governed threshold."""
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(
        canonical_book, access, cycle, item.id, item.current_revision_no, tier1="800"
    )
    detail = (computed.computation or {})["detail"]
    assert detail["irrbb_outlier_measure_pct"] == "14.594988"
    assert detail["outlier"] == "false"


def test_a_missing_governed_figure_is_a_typed_refusal_naming_the_row(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """D-024 §4: no default, and the operator is told which console row to fill."""
    canonical_book.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "irrbb_outlier_threshold_pct_tier1"
        )
    )
    canonical_book.commit()
    item = _create_irrbb(canonical_book, access, cycle)
    with pytest.raises(HTTPException) as caught:
        _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    body = _detail(caught)
    assert caught.value.status_code == 409
    assert body["error_code"] == "missing_parameter"
    assert body["param_code"] == "irrbb_outlier_threshold_pct_tier1"


def test_a_console_change_makes_an_approved_figure_stale(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    checker: IcaapAccess,
) -> None:
    """Staff lower the outlier threshold; the figure that rested on it says so."""
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    assert computed.stale is False
    approved = pillar2.approve_item(
        canonical_book,
        checker,
        cycle.id,
        item.id,
        IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Reviewed."),
    )
    assert approved.approval_current is True

    # What a console edit actually does: a NEWER approved generation, not an
    # in-place rewrite of the row the figure was computed against.
    canonical_book.add(
        RegulatoryParameter(
            scope_type="institution_class",
            scope_key="bank",
            param_code="irrbb_outlier_threshold_pct_tier1",
            jurisdiction_code="GH",
            value_numeric=Decimal("10"),
            unit="percent",
            source_citation="Test: staff tightened the outlier threshold.",
            confirmation_status="pending",
            effective_from=date(2025, 1, 1),
            status="approved",
            proposed_by="test-suite",
            approved_by="test-suite",
        )
    )
    canonical_book.commit()

    register = pillar2.get_register(canonical_book, access, cycle.id)
    entry = next(row for row in register.items if row.item_key == "irrbb")
    assert entry.stale is True
    assert any(reason.startswith("parameter_changed:") for reason in entry.stale_reasons)


def test_every_save_writes_an_unalterable_revision_and_approval_pins_one(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    checker: IcaapAccess,
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    listing = pillar2.list_revisions(canonical_book, access, cycle.id, item.id)
    numbers = [revision.revision_no for revision in listing.revisions]
    kinds = [revision.change_kind for revision in listing.revisions]
    assert numbers == sorted(numbers, reverse=True)
    assert kinds[-1] == "created" and "computed" in kinds
    assert all(revision.snapshot_sha256 for revision in listing.revisions)

    pillar2.approve_item(
        canonical_book,
        checker,
        cycle.id,
        item.id,
        IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Reviewed."),
    )
    # Recomputing moves the head on, so the approval is no longer current.
    after = _compute_irrbb(canonical_book, access, cycle, item.id, computed.current_revision_no)
    assert after.approval_current is False
    assert after.approved_revision_no == computed.current_revision_no


def test_the_person_who_produced_a_figure_cannot_approve_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    with pytest.raises(HTTPException) as caught:
        pillar2.approve_item(
            canonical_book,
            access,
            cycle.id,
            item.id,
            IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Mine."),
        )
    assert _detail(caught)["error_code"] == "self_approval"


def test_approving_a_revision_that_has_moved_on_is_refused(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    checker: IcaapAccess,
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    with pytest.raises(HTTPException) as caught:
        pillar2.approve_item(
            canonical_book,
            checker,
            cycle.id,
            item.id,
            IcaapPillar2Approve(revision_no=computed.current_revision_no - 1, note="Stale."),
        )
    body = _detail(caught)
    assert body["error_code"] == "revision_changed"
    assert body["current_revision_no"] == computed.current_revision_no


def test_an_uncomputed_figure_is_not_offered_for_approval(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    checker: IcaapAccess,
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    with pytest.raises(HTTPException) as caught:
        pillar2.approve_item(
            canonical_book,
            checker,
            cycle.id,
            item.id,
            IcaapPillar2Approve(revision_no=item.current_revision_no, note="Too early."),
        )
    body = _detail(caught)
    assert body["error_code"] == "item_not_approvable"
    assert body["reasons"] == ["status:not_computed"]


def test_a_missing_binding_is_a_state_with_a_plain_reason_not_an_error(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """An ICAAP in progress is allowed to be unfinished; readiness stops a freeze."""
    item = pillar2.create_item(
        canonical_book,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="fx",
            method="fx_nop_addon",
            reason="Quantify foreign exchange risk.",
        ),
    )
    computed = pillar2.compute_item(
        canonical_book,
        access,
        cycle.id,
        item.id,
        IcaapPillar2Compute(
            base_revision_no=item.current_revision_no, reason="Try it with what is linked."
        ),
    )
    assert computed.method_status == "not_computable"
    assert computed.status_detail is not None
    assert "Net open foreign exchange position" in computed.status_detail


def test_a_method_the_framework_does_not_allow_is_refused(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    with pytest.raises(HTTPException) as caught:
        pillar2.create_item(
            canonical_book,
            access,
            cycle.id,
            IcaapPillar2ItemCreate(
                component_key="irrbb",
                method="fx_nop_addon",
                reason="Wrong method for this risk.",
            ),
        )
    assert _detail(caught)["error_code"] == "method_not_allowed"


def test_a_method_that_is_not_built_yet_says_so_precisely(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is deferred today, so the refusal is proved against a stand-in.

    The standardised framework left ``DEFERRED_METHODS`` when P5-D landed its
    block resolver and method, as the granularity adjustment did before it. The
    refusal path still has to work for the next method that is declared before
    it is built, so it is exercised here rather than deleted along with its last
    subject — and the first assertion is what will fail if a method is ever
    declared and quietly left unimplemented.
    """
    assert set(DEFERRED_METHODS) == set()
    monkeypatch.setattr(
        pillar2, "DEFERRED_METHODS", frozenset({"irrbb_standardised_framework"})
    )
    with pytest.raises(HTTPException) as caught:
        pillar2.create_item(
            canonical_book,
            access,
            cycle.id,
            IcaapPillar2ItemCreate(
                component_key="irrbb",
                method="irrbb_standardised_framework",
                reason="A method the platform has not built.",
            ),
        )
    assert _detail(caught)["error_code"] == "method_not_available"


def test_one_component_can_carry_only_one_live_figure(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    _create_irrbb(canonical_book, access, cycle)
    with pytest.raises(HTTPException) as caught:
        _create_irrbb(canonical_book, access, cycle)
    assert _detail(caught)["error_code"] == "item_exists"


def test_a_board_judgement_needs_its_reasoning_and_evidence(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = pillar2.create_item(
        canonical_book,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="reputational",
            method="judgemental",
            source="judgemental",
            input_mode="manual_with_evidence",
            reason="Board judgement on reputational risk.",
        ),
    )
    with pytest.raises(HTTPException) as caught:
        pillar2.update_item(
            canonical_book,
            access,
            cycle.id,
            item.id,
            IcaapPillar2ItemUpdate(
                base_revision_no=item.current_revision_no,
                baseline_amount=Decimal("25"),
                reason="Record the Board's figure.",
            ),
        )
    assert _detail(caught)["error_code"] in {"rationale_required", "evidence_required"}


def test_retiring_a_figure_records_it_rather_than_deleting_it(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    pillar2.retire_item(
        canonical_book,
        access,
        cycle.id,
        item.id,
        IcaapRetire(reason="Superseded by the standardised framework."),
    )
    register = pillar2.get_register(canonical_book, access, cycle.id)
    assert register.items == []
    remaining = canonical_book.scalars(
        IcaapPillar2ItemRevision.__table__.select().where(
            IcaapPillar2ItemRevision.__table__.c.item_id == item.id
        )
    ).all()
    assert len(remaining) >= 2, "the revisions outlive the retirement"


def test_the_parameters_a_figure_rested_on_are_labelled_for_the_reader(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    item = _create_irrbb(canonical_book, access, cycle)
    computed = _compute_irrbb(canonical_book, access, cycle, item.id, item.current_revision_no)
    codes = {entry.param_code for entry in computed.parameters}
    assert "irrbb_outlier_threshold_pct_tier1" in codes
    assert computed.pending_parameters, "exposure-draft values are labelled pending"


def test_the_parameter_listing_names_every_governed_code_and_the_gaps(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    resolved = params.resolve_p2(canonical_book, access.bank, as_of=cycle.as_of_date)
    assert resolved.missing == frozenset(), "the hermetic fixture seeds every P2 code"
    representative = resolved.representative_codes(params.P2_PARAMETER_CODES)
    assert "ccr_name_bands_hhi" in representative


# --- the sovereign method, against the row the console actually ships ---------
#
# Until 2026-09-20 no service- or API-level test computed this method at all,
# and the domain suite's fixture used a different body SHAPE and different
# tenor keys from the seeded row. Both suites were green while the shipped
# parameter could not be parsed, so every bank asking for a sovereign add-on
# was told the governed row was missing (audit W2). These two drive the whole
# path — resolve the seeded row, parse it, compute — so the fixture can never
# again disagree with the catalogue unnoticed.


def _create_sovereign(db: Session, access: IcaapAccess, cycle: IcaapCycleRead):
    return pillar2.create_item(
        db,
        access,
        cycle.id,
        IcaapPillar2ItemCreate(
            component_key="sovereign",
            method="sovereign_stress_addon",
            input_mode="manual_with_evidence",
            rationale="Holdings supplied from the group securities register.",
            reason="Quantify sovereign restructuring risk.",
        ),
    )


#: One holding per shipped tenor bucket, so a renamed bucket shows up as a
#: conservative fill rather than passing silently.
SOVEREIGN_HOLDINGS: list[dict[str, Any]] = [
    {
        "key": "tbill",
        "currency_kind": "reporting",
        "tenor_bucket": "up_to_1y",
        "exposure": "100",
    },
    {
        "key": "note",
        "currency_kind": "reporting",
        "tenor_bucket": "1y_to_5y",
        "exposure": "400",
    },
    {
        "key": "eurobond",
        "currency_kind": "foreign",
        "tenor_bucket": "over_5y",
        "exposure": "200",
    },
]


def _compute_sovereign(db: Session, access: IcaapAccess, cycle: IcaapCycleRead, item_id, revision):
    return pillar2.compute_item(
        db,
        access,
        cycle.id,
        item_id,
        IcaapPillar2Compute(
            base_revision_no=revision,
            manual_inputs=IcaapPillar2ManualInputs(
                sovereign_holdings=[dict(entry) for entry in SOVEREIGN_HOLDINGS]
            ),
            reason="Compute from the securities register.",
        ),
    )


def test_the_sovereign_method_computes_from_the_seeded_console_row(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """The shipped ``sov_p2_haircut_pct`` body must be READABLE by its method.

    The expected figure is derived from the catalogue, not restated (D-024):
    haircut per holding times exposure, with no Pillar 1 RWA on these holdings.
    """
    grid = sovereign_domain.parse_haircut_grid(seed_body("sov_p2_haircut_pct"))
    expected = sum(
        (
            Decimal(str(entry["exposure"]))
            * grid[(str(entry["currency_kind"]), str(entry["tenor_bucket"]))]
            / Decimal(100)
            for entry in SOVEREIGN_HOLDINGS
        ),
        Decimal(0),
    )

    item = _create_sovereign(canonical_book, access, cycle)
    computed = _compute_sovereign(canonical_book, access, cycle, item.id, item.current_revision_no)

    assert computed.method_status == "computed"
    assert computed.baseline_amount == expected
    detail = (computed.computation or {})["detail"]
    assert detail["fill:tbill"] == sovereign_domain.FILL_EXACT
    assert detail["fill:eurobond"] == sovereign_domain.FILL_EXACT
    assert detail["conservative_fill"] == "false", "every shipped bucket was matched exactly"
    assert "sov_p2_haircut_pct" in {entry.param_code for entry in computed.parameters}


def test_a_deleted_sovereign_grid_is_the_only_thing_that_reads_as_missing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """``missing_parameter`` must mean the row is absent, never unparseable."""
    canonical_book.execute(
        delete(RegulatoryParameter).where(
            RegulatoryParameter.param_code == "sov_p2_haircut_pct"
        )
    )
    canonical_book.commit()
    item = _create_sovereign(canonical_book, access, cycle)
    with pytest.raises(HTTPException) as caught:
        _compute_sovereign(canonical_book, access, cycle, item.id, item.current_revision_no)
    body = _detail(caught)
    assert body["error_code"] == "missing_parameter"
    assert body["param_code"] == "sov_p2_haircut_pct"
