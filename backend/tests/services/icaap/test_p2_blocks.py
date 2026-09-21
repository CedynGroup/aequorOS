"""The P2 data blocks, the Table 5 read, and the refusals that protect them.

A register block has no sealed run to pin, so its binding pins a digest of its
own content. The first two tests are the load-bearing ones: a refresh that
changes nothing writes nothing, and a changed figure makes the block stale.
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
from app.domain.icaap import reconciliation as recon_domain
from app.models import BankReportingPeriod, User
from app.schemas.icaap import IcaapCycleRead, IcaapDataBlockCreate, IcaapDataBlockRefresh
from app.schemas.icaap_risk_capital import (
    IcaapPillar2Approve,
    IcaapPillar2Compute,
    IcaapPillar2ItemCreate,
    IcaapPillar2ManualInputs,
    IcaapRiskPut,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital
from app.services.icaap import blocks, pillar2, risks
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
            email="block.checker@example.test",
            display_name="Second Person",
        )
    )
    canonical_book.commit()
    return IcaapAccess(
        ctx=TenantContext(organization_id=ORG_1, actor_user_id=CHECKER, authorization_version=1),
        bank=access.bank,
    )


@pytest.fixture
def capital_run(canonical_book: Session, access: IcaapAccess) -> None:
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


def _create(db: Session, access: IcaapAccess, cycle: IcaapCycleRead, block_type: str):
    return blocks.create_block(db, access, cycle.id, IcaapDataBlockCreate(block_type=block_type))


def test_every_p2_register_block_can_be_created_and_binds_its_own_digest(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    for block_type in (
        "risk_register",
        "risk_appetite",
        "pillar2_summary",
        "audit_review",
        "challenge_log",
        "supervisory_addons",
    ):
        block = _create(canonical_book, access, cycle, block_type)
        assert block.status == "fresh", block_type
        assert block.current_binding is not None
        assert block.current_binding.source_key.startswith(f"computed:{block_type}:")


def test_a_refresh_that_changes_nothing_writes_nothing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = _create(canonical_book, access, cycle, "risk_register")
    assert block.current_binding is not None
    refreshed = blocks.refresh_block(
        canonical_book,
        access,
        cycle.id,
        block.id,
        IcaapDataBlockRefresh(reason="Nothing has changed."),
    )
    assert refreshed.outcome == "unchanged"
    assert refreshed.changed_facts == []


def test_scoring_a_risk_makes_the_register_block_stale_then_refreshable(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = _create(canonical_book, access, cycle, "risk_register")
    risks.put_risk(
        canonical_book,
        access,
        cycle.id,
        "credit",
        IcaapRiskPut(
            likelihood_score=4,
            impact_score=4,
            materiality_rationale="Concentrated book and a thin buffer.",
            reason="Score the risk.",
        ),
    )
    stale = blocks.get_block(canonical_book, access, cycle.id, block.id)
    assert stale.status == "stale"

    refreshed = blocks.refresh_block(
        canonical_book,
        access,
        cycle.id,
        block.id,
        IcaapDataBlockRefresh(reason="Pick up the scored risk."),
    )
    assert refreshed.outcome == "bound"
    assert {change.key for change in refreshed.changed_facts} >= {
        "assessed_risk_count",
        "material_risk_count",
    }
    binding = refreshed.block.current_binding
    assert binding is not None
    assert binding.facts["material_risk_count"].value == "1"


def test_the_pillar_two_block_publishes_the_registers_totals(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    checker: IcaapAccess,
) -> None:
    item = pillar2.create_item(
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
    computed = pillar2.compute_item(
        canonical_book,
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
    pillar2.approve_item(
        canonical_book,
        checker,
        cycle.id,
        item.id,
        IcaapPillar2Approve(revision_no=computed.current_revision_no, note="Reviewed."),
    )
    block = _create(canonical_book, access, cycle, "pillar2_summary")
    binding = block.current_binding
    assert binding is not None
    assert binding.facts["pillar2_total_baseline"].value == "116.7599"
    assert binding.facts["pillar2_all_approved"].value == "true"
    assert binding.facts["irrbb_outlier"].value == "true"
    # The dynamic per-row facts a section may quote by name.
    assert binding.facts["pillar2_irrbb_baseline"].value == "116.7599"


def test_the_fx_and_sovereign_blocks_say_plainly_what_is_missing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    fx = _create(canonical_book, access, cycle, "fx_position")
    assert fx.status == "unbound"
    assert "FX" in (fx.status_detail or "")

    sovereign = _create(canonical_book, access, cycle, "sovereign_exposures")
    assert sovereign.status == "unbound"
    assert sovereign.status_detail


def test_the_sovereign_block_reads_the_capital_runs_own_lines(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    capital_run: None,
) -> None:
    """Which lines are sovereign is a governed decision, not a guess at wording."""
    block = _create(canonical_book, access, cycle, "sovereign_exposures")
    # The canonical book may or may not carry a line in the governed category;
    # either way the block resolves against the sealed run and says which
    # categories it applied.
    if block.current_binding is not None:
        assert block.current_binding.source_key.startswith("run:")
        assert block.current_binding.source_ref["categories"] == ["gov_securities"]
    else:
        assert "sovereign" in (block.status_detail or "").lower()


def test_table5_says_what_is_missing_rather_than_composing_half_a_grid(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    read = pillar2.get_table5(canonical_book, access, cycle.id)
    assert read.available is False
    assert read.unavailable_reason is not None
    assert "Appendix II" in read.unavailable_reason
    assert read.rows == []


def test_a_basis_mismatch_from_the_domain_is_surfaced_as_a_typed_refusal(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The like-for-like type error must never be swallowed into a 500."""

    def explode(**_kwargs: Any):
        raise TypeError("basis_mismatch:overlay_stressed")

    monkeypatch.setattr(recon_domain, "pillar2_source_consistency", explode)
    with pytest.raises(HTTPException) as caught:
        pillar2.get_register(canonical_book, access, cycle.id)
    body = _detail(caught)
    assert caught.value.status_code == 409
    assert body["error_code"] == "basis_mismatch"
    assert body["argument"] == "overlay_stressed"


def test_an_unrelated_type_error_is_not_dressed_up_as_a_basis_mismatch(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(**_kwargs: Any):
        raise TypeError("something else entirely")

    monkeypatch.setattr(recon_domain, "pillar2_source_consistency", explode)
    with pytest.raises(TypeError):
        pillar2.get_register(canonical_book, access, cycle.id)


def test_the_appendix_adaptor_reads_the_runs_stored_table_five_shape() -> None:
    """The grid's Pillar 1 columns come from the run's own serialised tables.

    Only the Pillar 1 half is taken: the run's own Pillar 2 grid is left
    behind, because mixing it with the ICAAP register would put two different
    measurements of one risk in one row.
    """

    class _Binding:
        block_id = uuid4()
        seq = 1
        payload_sha256 = "a" * 64
        payload = {
            "raw": {
                "raw_appendix_ii": {
                    "table5_rwa": {
                        "car_target_pct": "13",
                        "rows": [
                            {
                                "label": label,
                                "credit_rwa": "800.000",
                                "operational_rwa": "150.000",
                                "market_rwa": "50.000",
                                "total_pillar1_rwa": "1000.000",
                                "pillar1_requirement": "130.000",
                                "pillar2": {"irrbb": "9.000", "other": None},
                                "total_capital_requirement": "139.000",
                            }
                            for label in ("current", "base_y1", "stress_y1")
                        ],
                    }
                }
            }
        }

    columns, reference = pillar2._appendix_columns(_Binding())  # noqa: SLF001
    assert [column.key for column in columns] == ["current", "base_y1", "stress_y1"]
    assert [column.basis for column in columns] == ["baseline", "baseline", "stressed"]
    assert columns[0].pillar1["credit_rwa"] == Decimal("800.000")
    assert "pillar2" not in columns[0].pillar1
    assert reference is not None
    assert reference["payload_sha256"] == "a" * 64


def test_the_adaptor_answers_nothing_when_no_appendix_is_linked() -> None:
    columns, reference = pillar2._appendix_columns(None)  # noqa: SLF001
    assert columns == []
    assert reference is None
