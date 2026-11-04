"""The review chain: order, rounds, send-backs, and who may decide.

The round rule itself is a pure domain function, so its case lives here; every
case that needs a database or the service layer is in
``test_p3_stage_chain_integration.py``.
"""

from __future__ import annotations

from app.icaap.domain import workflow as domain


class TestReturns:
    def test_the_round_rule_is_the_domains_and_is_not_restated(self) -> None:
        facts = (
            domain.DecisionFact(1, 1, "submitted", None, "a" * 64, "u1"),
            domain.DecisionFact(2, 1, "reviewed", None, "a" * 64, "u2"),
            domain.DecisionFact(3, 1, "returned", 2, "a" * 64, "u3"),
        )
        assert domain.reset_round_for(facts, 1) == 1
        # The return above reopened stage 2 onwards, so a round-1 review there
        # no longer counts while stage 1's submission still does.
        assert domain.reset_round_for(facts, 2) == 2
        assert domain.reset_round_for(facts, 3) == 2
        later = (*facts, domain.DecisionFact(3, 2, "returned", 1, "a" * 64, "u3"))
        assert domain.reset_round_for(later, 1) == 3
