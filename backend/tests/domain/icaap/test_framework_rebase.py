"""Moving a cycle to a successor version carries what maps, and flags what does not."""

from __future__ import annotations

import pytest

from app.domain.icaap.frameworks.rebase import RebaseNotPossible, plan_rebase
from app.domain.icaap.frameworks.registry import framework_digest
from app.domain.icaap.frameworks.schema import parse_framework
from tests.fixtures.icaap import synthetic_frameworks as synthetic


def test_a_rebase_needs_a_declared_lineage() -> None:
    with pytest.raises(RebaseNotPossible):
        plan_rebase(synthetic.version_one(), synthetic.unrelated())


def test_every_source_section_gets_a_decision() -> None:
    plan = plan_rebase(synthetic.version_one(), synthetic.version_two())
    moves = {move.from_key: move for move in plan.moves}
    assert set(moves) == {"overview", "risk_profile", "appetite", "models", "retired_topic"}
    assert moves["overview"].to_key == "summary"
    assert moves["overview"].relation == "equivalent"
    assert moves["overview"].needs_review is False
    assert moves["retired_topic"].to_key is None


def test_a_relation_that_changes_the_question_is_marked_for_review() -> None:
    """Merged, split and partial text answers a different question now."""
    plan = plan_rebase(synthetic.version_one(), synthetic.version_two())
    needs_review = {move.from_key for move in plan.moves if move.needs_review}
    assert needs_review == {"risk_profile", "appetite", "models"}
    assert set(plan.needs_review) == {"risk_and_appetite", "models"}


def test_sections_nothing_maps_into_start_empty() -> None:
    plan = plan_rebase(synthetic.version_one(), synthetic.version_two())
    assert plan.new_sections == ("new_topic",)


def test_checklist_state_carries_only_for_requirements_that_still_exist() -> None:
    plan = plan_rebase(synthetic.version_one(), synthetic.version_two())
    assert plan.carried_items == {"keep_one", "keep_two", "keep_three"}
    assert plan.dropped_items == {"drop_one", "drop_two", "drop_three"}


def test_a_successor_that_forgets_a_section_is_refused() -> None:
    """Silence is not a mapping: an unmapped section must say ``unmapped``."""
    raw = synthetic.version_two_raw()
    raw["section_key_map"] = [
        entry for entry in raw["section_key_map"] if entry["from"] != "retired_topic"
    ]

    target = parse_framework(raw, digest=framework_digest(raw))
    with pytest.raises(RebaseNotPossible) as caught:
        plan_rebase(synthetic.version_one(), target)
    assert "retired_topic" in str(caught.value)


def test_the_plan_names_both_versions() -> None:
    plan = plan_rebase(synthetic.version_one(), synthetic.version_two())
    assert plan.source == ("zz_example", "1.0")
    assert plan.target == ("zz_example", "2.0")
