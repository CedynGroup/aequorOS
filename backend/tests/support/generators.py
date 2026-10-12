"""Construct bounded synthetic review histories; domain wrappers own stage semantics."""

from dataclasses import dataclass
from typing import Literal

from hypothesis import strategies as st

from app.domain.workflow.chain import DecisionFact


@dataclass(frozen=True)
class ReviewHistory:
    decisions: tuple[DecisionFact, ...]
    current_round: int


@st.composite
def review_histories(
    draw: st.DrawFn, *, review_decision: Literal["approved", "reviewed"]
) -> ReviewHistory:
    """Three distinct officers, with up to two documented returns and fresh reviews.

    Source: domain/workflow/chain.py DecisionFact and send-back contract.
    A fixed recipe creates valid histories without querying transition helpers.
    UUIDs are synthetic and reproducible through Hypothesis. Two returns bound
    test size; it is not a production limit.
    """
    officers = draw(st.lists(st.uuids(version=4), min_size=3, max_size=3, unique=True))
    digest = draw(st.binary(min_size=32, max_size=32)).hex()
    returns = draw(st.lists(st.integers(1, 2), max_size=2))
    decisions: list[DecisionFact] = []
    round_no = 1
    start = 1
    for target in [*returns, None]:
        for stage in range(start, 3):
            decisions.append(
                DecisionFact(
                    stage_seq=stage,
                    round=round_no,
                    decision="submitted" if stage == 1 else review_decision,
                    return_to_seq=None,
                    review_digest=digest,
                    decided_by=str(officers[stage - 1]),
                )
            )
        decisions.append(
            DecisionFact(
                stage_seq=3,
                round=round_no,
                decision="approved" if target is None else "returned",
                return_to_seq=target,
                review_digest=digest,
                decided_by=str(officers[2]),
            )
        )
        if target is not None:
            start = target
            round_no += 1
    return ReviewHistory(tuple(decisions), round_no)
