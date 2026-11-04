"""Generator validity is executable behavior, not inspection of strategy source."""

from calendar import monthrange
from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given

from app.domain.workflow import chain
from app.reporting.domain import workflow
from app.reporting.service.anchors import anchor_dates, anchor_window
from app.reporting.service.registry import ACCRA_TZ
from tests.reporting.testing.assertions import assert_review_order_independent
from tests.reporting.testing.generators import (
    CHECKPOINTS,
    OUTCOMES,
    FilingCalendarCase,
    filing_calendar_cases,
    filing_checkpoints,
    filing_review_histories,
    filing_stages,
    regulator_outcomes,
    stage_titles,
)
from tests.support.generators import ReviewHistory


@given(title=stage_titles())
def test_generated_filing_chain_is_runnable(title: str) -> None:
    stages = workflow.validate_stages([stage.model_dump() for stage in filing_stages(title)])
    assert workflow.transmit_seq(stages) == len(stages)
    assert stages[0].title == title


@given(checkpoint=filing_checkpoints(), outcome=regulator_outcomes())
def test_checkpoints_and_regulator_outcomes_are_valid(checkpoint: str, outcome: str) -> None:
    assert checkpoint in CHECKPOINTS
    assert outcome in OUTCOMES


@given(case=filing_calendar_cases())
def test_calendar_cases_use_regulator_anchors_and_accra_boundaries(
    case: FilingCalendarCase,
) -> None:
    definition, anchor = case.definition, case.reporting_date
    assert anchor in anchor_dates(definition, anchor_window(anchor))
    if definition.frequency in {"monthly", "quarterly", "semiannual", "annual"}:
        assert anchor.day == monthrange(anchor.year, anchor.month)[1]
    assert case.observed_at.tzinfo == ZoneInfo(ACCRA_TZ)
    assert case.observed_at.utcoffset() == timedelta(0)
    cutoff = time.fromisoformat(definition.due_time) if definition.due_time else time.min
    boundary = datetime.combine(case.due_date or anchor, cutoff, ZoneInfo(ACCRA_TZ))
    assert case.observed_at - boundary in {timedelta(seconds=n) for n in (-1, 0, 1)}
    if definition.deadline_parameter:
        assert case.due_date is None, "A governed deadline must never be fabricated"
    else:
        assert case.due_date is not None
        assert case.due_date == definition.deadline_rule(anchor)
        assert abs((case.observed_at.date() - case.due_date).days) <= 1


@given(history=filing_review_histories())
def test_review_histories_are_valid_and_order_independent(history: ReviewHistory) -> None:
    decisions = history.decisions
    # Each history is fully reviewed, including fresh decisions after every return.
    assert workflow.chain_complete(
        workflow.default_stages(), decisions, current_round=history.current_round
    )
    assert len({fact.decided_by for fact in decisions}) == 3
    assert len({(fact.stage_seq, fact.round) for fact in decisions}) == len(decisions)
    assert all(
        fact.return_to_seq is None or fact.return_to_seq < fact.stage_seq for fact in decisions
    )
    for stage in range(1, 4):
        actual = chain.stage_decided(decisions, stage, current_round=history.current_round)
        reordered = chain.stage_decided(
            tuple(reversed(decisions)), stage, current_round=history.current_round
        )
        assert_review_order_independent(actual, reordered)
    renamed = tuple(replace(fact, decided_by=f"synthetic-{fact.decided_by}") for fact in decisions)
    assert workflow.chain_complete(
        workflow.default_stages(), renamed, current_round=history.current_round
    )
