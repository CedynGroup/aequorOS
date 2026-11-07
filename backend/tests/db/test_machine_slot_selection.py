"""Rule-victim selection in the state machines must follow the drawn choices.

The authorization state machines key their bindings and exceptions by
server-generated ``uuid4`` values. Selecting a victim by uuid-string order
made the machines' state evolution depend on unseeded randomness: two runs of
identical drawn choices could revoke (or approve) different logical bindings,
which changed precondition emptiness on a replayed choice prefix and raised
``FlakyStrategyDefinition`` under deep Hypothesis budgets.
``_slot_pick`` selects in dict insertion order — rule-execution order —
which is fully determined by the drawn choices.
"""

from uuid import UUID

from tests.db.test_authorization_effective_access_properties_integration import _slot_pick

#: Three grants created by the same choice-driven history: the maker's, the
#: checker's, then the reader's. The two runs carry identical specs in
#: identical insertion order and differ only in the uuid values the server
#: minted for them.
_RUN_ONE = {UUID(int=1): "maker-grant", UUID(int=2): "checker-grant", UUID(int=3): "reader-grant"}
_RUN_TWO = {
    UUID(int=30): "maker-grant",
    UUID(int=20): "checker-grant",
    UUID(int=10): "reader-grant",
}


def _victims_in_uuid_order(entries: dict[UUID, str]) -> list[str]:
    """The superseded selection: uuid-string order (the documented hazard)."""
    return [entries[key] for key in sorted(entries, key=str)]


def test_superseded_uuid_ordering_diverges_for_identical_choice_histories() -> None:
    """The old ordering picked different logical victims for the same draws."""
    assert _victims_in_uuid_order(_RUN_ONE) == ["maker-grant", "checker-grant", "reader-grant"]
    assert _victims_in_uuid_order(_RUN_TWO) == ["reader-grant", "checker-grant", "maker-grant"]


def test_slot_pick_selects_the_same_logical_victim_under_new_uuids() -> None:
    """Insertion-order selection ignores the uuid values entirely."""
    for slot in range(6):
        victim_one = _RUN_ONE[_slot_pick(_RUN_ONE, slot)]
        victim_two = _RUN_TWO[_slot_pick(_RUN_TWO, slot)]
        assert victim_one == victim_two, f"slot {slot}: {victim_one} != {victim_two}"
