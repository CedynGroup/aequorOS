"""Refreshing, pinning and retiring a bound figure."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.domain.icaap.blocks import BlockStatus
from app.models import AuditEvent, BankReportingPeriod
from app.models.icaap import IcaapBlockBinding
from app.schemas.icaap import (
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapDataBlockPin,
    IcaapDataBlockRefresh,
    IcaapDataBlockRetire,
    IcaapSectionWorkingSave,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital
from app.services.icaap import blocks, resolvers, sections
from tests.services.icaap.conftest import AS_OF

PIN_REASON = "The Board reviewed the December figures and approved this report on them."


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def _period(db: Session, access: IcaapAccess) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None
    return period


def _run_capital(db: Session, access: IcaapAccess) -> None:
    regulatory_capital.create_capital_run(
        db,
        access.ctx,
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=_period(db, access).id, scenario_code="baseline"
        ),
    )
    db.commit()


@pytest.fixture
def bound_block(canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead):
    _run_capital(canonical_book, access)
    return blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="capital_position")
    )


def test_a_newer_run_makes_the_bound_figure_stale(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    assert bound_block.status == BlockStatus.FRESH.value
    _run_capital(canonical_book, access)
    refreshed = blocks.get_block(canonical_book, access, cycle.id, bound_block.id)
    assert refreshed.status == BlockStatus.STALE.value
    assert refreshed.status_detail == "Newer figures are available."


def test_a_refresh_binds_the_newer_run_and_reports_what_moved(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    before = bound_block.current_binding
    assert before is not None
    _run_capital(canonical_book, access)
    result = blocks.refresh_block(
        canonical_book,
        access,
        cycle.id,
        bound_block.id,
        IcaapDataBlockRefresh(reason="Picking up the corrected run"),
    )
    assert result.outcome == "bound"
    assert result.block.status == BlockStatus.FRESH.value
    after = result.block.current_binding
    assert after is not None
    assert after.seq == before.seq + 1
    assert after.source_key != before.source_key
    stored = canonical_book.scalars(
        select(IcaapBlockBinding).where(IcaapBlockBinding.block_id == bound_block.id)
    ).all()
    assert len(stored) == 2, "bindings are append-only"


def test_a_refresh_that_changes_nothing_writes_nothing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    """Otherwise every page load would grow the evidence trail."""
    result = blocks.refresh_block(
        canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockRefresh()
    )
    assert result.outcome == "unchanged"
    assert result.changed_facts == []
    stored = canonical_book.scalars(
        select(IcaapBlockBinding).where(IcaapBlockBinding.block_id == bound_block.id)
    ).all()
    assert len(stored) == 1


def test_a_refresh_with_no_source_reports_why_rather_than_failing(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="reverse_stress")
    )
    result = blocks.refresh_block(
        canonical_book, access, cycle.id, block.id, IcaapDataBlockRefresh()
    )
    assert result.outcome == "unavailable"
    assert result.reason
    assert result.block.status == BlockStatus.UNBOUND.value


def test_pinning_records_the_judgement_and_holds_the_figures(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    _run_capital(canonical_book, access)
    pinned = blocks.pin_block(
        canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockPin(reason=PIN_REASON)
    )
    assert pinned.status == BlockStatus.PINNED.value
    assert pinned.pin_reason == PIN_REASON
    assert pinned.pinned_at is not None
    events = canonical_book.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "icaap.block.pinned")
    ).all()
    assert events and events[-1].details["reason"] == PIN_REASON


def test_unpinning_shows_the_newer_figures_again(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    _run_capital(canonical_book, access)
    blocks.pin_block(
        canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockPin(reason=PIN_REASON)
    )
    unpinned = blocks.unpin_block(canonical_book, access, cycle.id, bound_block.id)
    assert unpinned.status == BlockStatus.STALE.value
    assert unpinned.pin_reason is None


def test_a_refresh_supersedes_a_pin(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    """The pin was a choice about the binding the refresh just replaced."""
    _run_capital(canonical_book, access)
    blocks.pin_block(
        canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockPin(reason=PIN_REASON)
    )
    result = blocks.refresh_block(
        canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockRefresh()
    )
    assert result.outcome == "bound"
    assert result.block.pin_reason is None
    assert result.block.status == BlockStatus.FRESH.value


def test_a_block_that_was_never_linked_cannot_be_pinned(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="reverse_stress")
    )
    with pytest.raises(HTTPException) as caught:
        blocks.pin_block(
            canonical_book, access, cycle.id, block.id, IcaapDataBlockPin(reason=PIN_REASON)
        )
    assert _detail(caught)["error_code"] == "pin_not_allowed"


def test_figures_whose_inputs_were_withdrawn_cannot_be_kept(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    bound_block,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(resolvers, "run_withdrawn", lambda *_args: True)
    withdrawn = blocks.get_block(canonical_book, access, cycle.id, bound_block.id)
    assert withdrawn.status == BlockStatus.SOURCE_WITHDRAWN.value
    with pytest.raises(HTTPException) as caught:
        blocks.pin_block(
            canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockPin(reason=PIN_REASON)
        )
    assert _detail(caught)["error_code"] == "pin_not_allowed"


def test_a_figure_still_used_in_the_text_cannot_be_retired(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    document = {
        "type": "doc",
        "content": [{"type": "dataBlock", "attrs": {"blockId": str(bound_block.id)}}],
    }
    sections.save_working(
        canonical_book,
        access,
        cycle.id,
        "executive_summary",
        IcaapSectionWorkingSave(doc=document, base_rev=0),
    )
    with pytest.raises(HTTPException) as caught:
        blocks.retire_block(
            canonical_book,
            access,
            cycle.id,
            bound_block.id,
            IcaapDataBlockRetire(reason="Replaced"),
        )
    assert _detail(caught)["error_code"] == "block_referenced"


def test_an_unused_figure_can_be_retired(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    retired = blocks.retire_block(
        canonical_book,
        access,
        cycle.id,
        bound_block.id,
        IcaapDataBlockRetire(reason="Superseded by the Pillar 1 table"),
    )
    assert retired.retired_at is not None
    with pytest.raises(HTTPException):
        blocks.refresh_block(
            canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockRefresh()
        )


def test_a_computed_figure_cannot_be_refreshed_by_hand(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = blocks.create_block(
        canonical_book, access, cycle.id, IcaapDataBlockCreate(block_type="financials")
    )
    with pytest.raises(HTTPException) as caught:
        blocks.refresh_block(canonical_book, access, cycle.id, block.id, IcaapDataBlockRefresh())
    assert _detail(caught)["error_code"] == "block_is_manual"


def test_the_binding_history_is_readable_newest_first(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, bound_block
) -> None:
    _run_capital(canonical_book, access)
    blocks.refresh_block(canonical_book, access, cycle.id, bound_block.id, IcaapDataBlockRefresh())
    listing = blocks.list_bindings(canonical_book, access, cycle.id, bound_block.id)
    assert [entry.seq for entry in listing.bindings] == [2, 1]
    assert all(entry.payload is None for entry in listing.bindings)
