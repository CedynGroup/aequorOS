"""Block catalogue integrity and the staleness truth table."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.icaap.blocks import (
    BLOCK_CATALOGUE,
    BLOCK_TYPES,
    MANUAL_BLOCK_TYPES,
    P1_BLOCK_TYPES,
    SOURCE_KINDS,
    BindingSnapshot,
    BlockSpec,
    BlockStatus,
    SourceProbe,
    blocks_freeze,
    evaluate_status,
)

AS_OF = date(2026, 12, 31)
OTHER = date(2025, 12, 31)


def spec(block_type: str = "capital_position") -> BlockSpec:
    return BLOCK_CATALOGUE[block_type]


def binding(
    *, seq: int = 1, key: str = "run:1", as_of: date | None = AS_OF, manual: bool = False
) -> BindingSnapshot:
    return BindingSnapshot(seq=seq, source_key=key, source_as_of=as_of, manual=manual)


# --- catalogue -------------------------------------------------------------


def test_the_p1_catalogue_is_the_twelve_engine_blocks_plus_two_manual_tables() -> None:
    assert P1_BLOCK_TYPES <= BLOCK_TYPES
    assert {"financials", "manual_table"} == MANUAL_BLOCK_TYPES
    assert "capital_position" in P1_BLOCK_TYPES
    # Declared for the framework to cite, but not creatable yet.
    for later in ("risk_register", "pillar2_summary", "challenge_log", "irrbb_sf"):
        assert later in BLOCK_TYPES
        assert later not in P1_BLOCK_TYPES


def test_the_standardised_framework_block_is_declared_for_its_phase() -> None:
    assert BLOCK_CATALOGUE["irrbb_sf"].phase == "P5"


@pytest.mark.parametrize("block_type", sorted(BLOCK_TYPES))
def test_each_block_declares_a_coherent_specification(block_type: str) -> None:
    entry = BLOCK_CATALOGUE[block_type]
    assert entry.type == block_type
    assert entry.title
    assert set(entry.source_kinds) <= set(SOURCE_KINDS)
    keys = [fact.key for fact in entry.facts]
    assert len(keys) == len(set(keys))
    if entry.dynamic_facts:
        assert not entry.facts
    if entry.manual:
        assert entry.source_kinds == ("manual",)
        assert entry.as_of_rule == "none"


def test_supervisory_add_ons_can_never_be_published() -> None:
    """D-023: a regulator's private instruction is not the bank's to disclose."""
    assert BLOCK_CATALOGUE["supervisory_addons"].never_public is True
    others = [s for s in BLOCK_CATALOGUE.values() if s.type != "supervisory_addons"]
    assert not any(entry.never_public for entry in others)


# --- staleness -------------------------------------------------------------


def test_a_block_nobody_has_linked_is_unbound() -> None:
    status = evaluate_status(
        spec(), None, SourceProbe(current_key="run:1"), cycle_as_of=AS_OF, pinned_seq=None
    )
    assert status is BlockStatus.UNBOUND


def test_a_block_showing_the_current_source_is_fresh() -> None:
    status = evaluate_status(
        spec(), binding(), SourceProbe(current_key="run:1"), cycle_as_of=AS_OF, pinned_seq=None
    )
    assert status is BlockStatus.FRESH


def test_a_newer_source_makes_the_block_stale() -> None:
    status = evaluate_status(
        spec(), binding(), SourceProbe(current_key="run:2"), cycle_as_of=AS_OF, pinned_seq=None
    )
    assert status is BlockStatus.STALE


def test_a_pin_holds_the_block_at_the_figures_somebody_chose() -> None:
    status = evaluate_status(
        spec(), binding(seq=3), SourceProbe(current_key="run:2"), cycle_as_of=AS_OF, pinned_seq=3
    )
    assert status is BlockStatus.PINNED


def test_a_pin_on_an_older_binding_does_not_hold_the_current_one() -> None:
    status = evaluate_status(
        spec(), binding(seq=4), SourceProbe(current_key="run:2"), cycle_as_of=AS_OF, pinned_seq=3
    )
    assert status is BlockStatus.STALE


def test_a_withdrawn_source_beats_a_pin() -> None:
    """Nobody can choose to keep figures whose inputs have been withdrawn."""
    status = evaluate_status(
        spec(),
        binding(seq=2),
        SourceProbe(current_key="run:1", withdrawn=True),
        cycle_as_of=AS_OF,
        pinned_seq=2,
    )
    assert status is BlockStatus.SOURCE_WITHDRAWN


def test_figures_for_another_date_are_reported_as_such() -> None:
    status = evaluate_status(
        spec(),
        binding(as_of=OTHER),
        SourceProbe(current_key="run:1"),
        cycle_as_of=AS_OF,
        pinned_seq=None,
    )
    assert status is BlockStatus.AS_OF_MISMATCH


def test_figures_for_another_date_may_still_be_pinned() -> None:
    status = evaluate_status(
        spec(),
        binding(seq=1, as_of=OTHER),
        SourceProbe(current_key="run:1"),
        cycle_as_of=AS_OF,
        pinned_seq=1,
    )
    assert status is BlockStatus.PINNED


def test_a_source_that_has_gone_away_is_reported_as_missing() -> None:
    status = evaluate_status(
        spec(), binding(), SourceProbe(current_key=None), cycle_as_of=AS_OF, pinned_seq=None
    )
    assert status is BlockStatus.SOURCE_MISSING


def test_a_block_with_no_as_of_rule_ignores_the_cycle_date() -> None:
    status = evaluate_status(
        spec("institution_profile"),
        binding(key="register:abc", as_of=None),
        SourceProbe(current_key="register:abc"),
        cycle_as_of=AS_OF,
        pinned_seq=None,
    )
    assert status is BlockStatus.FRESH


def test_a_manual_table_is_exactly_what_somebody_typed() -> None:
    status = evaluate_status(
        spec("financials"),
        binding(key="manual:1", as_of=None, manual=True),
        SourceProbe(current_key=None),
        cycle_as_of=AS_OF,
        pinned_seq=None,
    )
    assert status is BlockStatus.FRESH


@pytest.mark.parametrize(
    ("status", "blocks"),
    [
        (BlockStatus.UNBOUND, True),
        (BlockStatus.STALE, True),
        (BlockStatus.AS_OF_MISMATCH, True),
        (BlockStatus.SOURCE_WITHDRAWN, True),
        (BlockStatus.SOURCE_MISSING, True),
        (BlockStatus.FRESH, False),
        (BlockStatus.PINNED, False),
    ],
)
def test_which_states_stop_a_freeze(status: BlockStatus, blocks: bool) -> None:
    assert blocks_freeze(status) is blocks
