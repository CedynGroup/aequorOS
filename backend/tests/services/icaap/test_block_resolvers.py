"""Block resolvers read sealed state for the cycle's exact date — and nothing else.

The tripwire test at the bottom is the load-bearing one. Every resolver runs
with the live plane monkeypatched to explode; if any of them reaches for
``live_metrics`` or the live fact period, the test fails rather than a filed
report quietly citing a figure the worker recomputed this morning.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess, TenantContext
from app.domain.icaap.blocks import P1_BLOCK_TYPES, BlockStatus
from app.models import BankReportingPeriod
from app.schemas.icaap import (
    IcaapCycleRead,
    IcaapDataBlockCreate,
    IcaapManualColumn,
    IcaapManualRow,
    IcaapManualTablePut,
)
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.services import regulatory_capital
from app.services.icaap import blocks
from tests.services.icaap.conftest import AS_OF


def _detail(caught: pytest.ExceptionInfo[HTTPException]) -> dict[str, Any]:
    """The refusal body, narrowed once so each assertion reads plainly."""
    detail = caught.value.detail
    assert isinstance(detail, dict)
    return detail


def _ctx(access: IcaapAccess) -> TenantContext:
    return access.ctx


@pytest.fixture
def capital_run(canonical_book: Session, access: IcaapAccess) -> None:
    """A sealed baseline capital run for the cycle's own year end."""
    period = canonical_book.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == access.bank.id,
            BankReportingPeriod.period_end == AS_OF,
        )
    )
    assert period is not None, "the canonical book has a 31 December period"
    regulatory_capital.create_capital_run(
        canonical_book,
        _ctx(access),
        access.bank.id,
        RegulatoryRunCreate(
            module="capital", reporting_period_id=period.id, scenario_code="baseline"
        ),
    )
    canonical_book.commit()


def _create(
    db: Session, access: IcaapAccess, cycle: IcaapCycleRead, block_type: str, **params: Any
):
    return blocks.create_block(
        db,
        access,
        cycle.id,
        IcaapDataBlockCreate(block_type=block_type, params=params),
    )


def test_the_capital_block_binds_the_sealed_run_for_the_cycles_year_end(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, capital_run: None
) -> None:
    block = _create(canonical_book, access, cycle, "capital_position")
    assert block.status == BlockStatus.FRESH.value
    binding = block.current_binding
    assert binding is not None
    assert binding.source_kind == "run"
    assert binding.source_as_of == AS_OF
    assert binding.source_ref["input_hash"]
    assert binding.source_key.startswith("run:")
    assert binding.facts["car_pct"].value is not None
    assert binding.facts["car_pct"].kind == "ratio_pct"


def test_figures_are_strings_and_a_missing_one_is_never_zero(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, capital_run: None
) -> None:
    block = _create(canonical_book, access, cycle, "capital_position")
    binding = block.current_binding
    assert binding is not None
    for fact in binding.facts.values():
        assert fact.value is None or isinstance(fact.value, str)
        assert fact.value != "0" or fact.kind != "ratio_pct"


def test_the_payload_carries_the_currency_from_the_institution(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, capital_run: None
) -> None:
    detail = blocks.get_block(
        canonical_book,
        access,
        cycle.id,
        _create(canonical_book, access, cycle, "capital_position").id,
    )
    payload = detail.current_binding.payload if detail.current_binding else None
    assert payload is not None
    assert payload["schema"] == "icaap-block-payload-v1"
    assert payload["unit"]["currency"] == access.bank.currency
    assert payload["tables"]


def test_the_pillar_one_block_reports_the_three_risk_types(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, capital_run: None
) -> None:
    block = _create(canonical_book, access, cycle, "pillar1_rwa")
    binding = block.current_binding
    assert binding is not None
    assert {"credit_rwa", "market_rwa", "operational_rwa", "total_rwa"} <= set(binding.facts)


def test_the_profile_block_pins_the_register_digest(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = _create(canonical_book, access, cycle, "institution_profile")
    if block.status == BlockStatus.UNBOUND.value:
        # No profile register on the fixture bank: the card says so in words.
        assert block.status_detail
        return
    binding = block.current_binding
    assert binding is not None
    assert binding.source_kind == "register"
    assert binding.source_key.startswith("register:")
    assert binding.source_as_of is None


def test_the_concentration_block_reports_the_index_on_a_zero_to_one_scale(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = _create(canonical_book, access, cycle, "concentration")
    if block.status == BlockStatus.UNBOUND.value:
        assert block.status_detail
        return
    binding = block.current_binding
    assert binding is not None
    assert binding.source_kind == "computed"
    for key, fact in binding.facts.items():
        if key.startswith("hhi_") and fact.value is not None:
            assert Decimal(fact.value) <= Decimal("1")
    payload = blocks.get_block(canonical_book, access, cycle.id, block.id).current_binding
    assert payload is not None and payload.payload is not None
    assert not any("_ghs" in key for key in payload.payload)


@pytest.mark.parametrize(
    "block_type",
    [
        "appendix_ii",
        "stress_narratives",
        "reverse_stress",
        "capital_plan",
        "ilaap",
        "irrbb",
        "management_actions",
    ],
)
def test_a_block_with_no_sealed_source_says_so_in_plain_language(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead, block_type: str
) -> None:
    """An absent source is a state to report, not an error to raise."""
    block = _create(canonical_book, access, cycle, block_type)
    assert block.status == BlockStatus.UNBOUND.value
    assert block.status_detail
    assert block.status_detail[0].isupper() or block.status_detail.startswith("no ")
    assert "Traceback" not in block.status_detail


def test_a_manual_table_holds_what_somebody_typed_and_blanks_stay_blank(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    block = _create(canonical_book, access, cycle, "financials")
    saved = blocks.put_manual_table(
        canonical_book,
        access,
        cycle.id,
        block.id,
        IcaapManualTablePut(
            columns=[
                IcaapManualColumn(key="fy0", label="2025", kind="amount"),
                IcaapManualColumn(key="fy1", label="2024", kind="amount"),
            ],
            rows=[
                IcaapManualRow(
                    key="profit_before_tax",
                    label="Profit before tax",
                    fact_key="profit_before_tax",
                    cells={"fy0": "1250000.00", "fy1": None},
                )
            ],
            reason="From the audited accounts",
        ),
    )
    binding = saved.current_binding
    assert binding is not None
    assert binding.source_kind == "manual"
    assert binding.facts["profit_before_tax_fy0"].value == "1250000.00"
    assert "profit_before_tax_fy1" not in binding.facts
    assert saved.status == BlockStatus.FRESH.value


def test_a_manual_table_cannot_carry_a_value_in_a_column_it_lacks(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:

    block = _create(canonical_book, access, cycle, "manual_table")
    with pytest.raises(HTTPException) as caught:
        blocks.put_manual_table(
            canonical_book,
            access,
            cycle.id,
            block.id,
            IcaapManualTablePut(
                columns=[IcaapManualColumn(key="fy0", label="2025", kind="amount")],
                rows=[IcaapManualRow(key="row", label="Row", cells={"fy9": "1"})],
                reason="Typo",
            ),
        )
    assert _detail(caught)["error_code"] == "invalid_manual_cell"


def test_a_later_phase_block_cannot_be_created_yet(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """P3's review trail is still declared only; P5's framework block is not.

    The standardised framework left this set when its resolver landed (P5-D),
    the same way the full granularity adjustment left ``DEFERRED_METHODS``: the
    block can now be created, and it answers with a binding or a named refusal
    instead of ``block_type_unavailable``.
    """

    with pytest.raises(HTTPException) as caught:
        _create(canonical_book, access, cycle, "workflow_summary")
    assert _detail(caught)["error_code"] == "block_type_unavailable"
    # No standardised framework run exists for this cycle, so the block is
    # created UNBOUND and says why — which is a different answer from "that
    # kind of figure does not exist yet".
    block = _create(canonical_book, access, cycle, "irrbb_sf")
    assert block.status == BlockStatus.UNBOUND.value
    assert block.current_binding is None


def test_one_block_of_each_kind_per_cycle(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:

    _create(canonical_book, access, cycle, "capital_position")
    with pytest.raises(HTTPException) as caught:
        _create(canonical_book, access, cycle, "capital_position")
    assert _detail(caught)["error_code"] == "block_exists"


_LIVE_PLANE_SEAMS = (
    ("app.services.live_block", "live_block"),
    ("app.services.live_state", "current_fact_period_or_409"),
    ("app.services.fact_derivation", "derive_current_facts"),
)


def test_no_resolver_reaches_for_the_live_plane(
    canonical_book: Session,
    access: IcaapAccess,
    cycle: IcaapCycleRead,
    capital_run: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Board-approved figure is one that was computed, reviewed and sealed.

    The live plane is a monitoring surface the worker rewrites continuously. If
    a resolver read it, a frozen report could cite a number nobody approved.
    """

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an ICAAP block resolver reached into the live plane")

    for module_name, attribute in _LIVE_PLANE_SEAMS:
        module = importlib.import_module(module_name)
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, explode)

    for block_type in sorted(P1_BLOCK_TYPES):
        created = _create(canonical_book, access, cycle, block_type)
        blocks.get_block(canonical_book, access, cycle.id, created.id)
    listing = blocks.list_blocks(canonical_book, access, cycle.id, include_payload=True)
    assert {entry.block_type for entry in listing.blocks} == P1_BLOCK_TYPES
