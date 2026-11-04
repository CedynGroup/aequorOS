"""ICAAP generators obey the public stage and document contracts."""

from hypothesis import given

from app.domain.workflow import chain
from app.icaap.domain import prosemirror, workflow
from tests.icaap.testing.assertions import assert_review_order_independent
from tests.icaap.testing.generators import (
    CHECKPOINTS,
    freeze_checkpoints,
    icaap_review_histories,
    icaap_stages,
    report_narratives,
)
from tests.reporting.testing.generators import stage_titles
from tests.support.generators import ReviewHistory


@given(title=stage_titles(), checkpoint=freeze_checkpoints())
def test_icaap_chain_and_checkpoints_are_valid(title: str, checkpoint: str) -> None:
    stages = workflow.validate_stages([stage.model_dump() for stage in icaap_stages(title)])
    assert stages[-1].decision_kind == "attest"
    assert sum(stage.freeze_on_approve for stage in stages) == 1
    assert checkpoint in CHECKPOINTS


@given(text=report_narratives())
def test_report_narratives_round_trip_through_prosemirror(text: str) -> None:
    doc = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }
    assert prosemirror.plain_text(prosemirror.validate_doc(doc)) == text


@given(history=icaap_review_histories())
def test_icaap_review_histories_keep_officers_and_rounds_consistent(history: ReviewHistory) -> None:
    decisions = history.decisions
    assert len({fact.decided_by for fact in decisions}) == 3
    assert len({(fact.stage_seq, fact.round) for fact in decisions}) == len(decisions)
    for stage in range(1, 4):
        actual = chain.stage_decided(decisions, stage, current_round=history.current_round)
        assert actual, f"ICAAP: generated stage {stage} has no standing decision"
        assert_review_order_independent(
            actual,
            chain.stage_decided(
                tuple(reversed(decisions)), stage, current_round=history.current_round
            ),
        )
    makers = chain.makers(decisions, content_authors=())
    checkers = chain.checkers(decisions, current_round=history.current_round, exclude_seq=None)
    assert makers.isdisjoint(checkers), f"ICAAP: author/checker overlap in {history}"
    assert len(checkers) == 2
