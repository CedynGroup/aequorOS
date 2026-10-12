"""Synthetic governance inputs; no production or customer narratives."""

from hypothesis import strategies as st

from app.icaap.schemas.workspace import IcaapStageInput
from tests.support.generators import ReviewHistory, review_histories

CHECKPOINTS = ("draft", "review", "approval", "ready", "frozen", "archived")


def freeze_checkpoints() -> st.SearchStrategy[str]:
    """Reachable complete-framework prefixes; tests/fixtures/icaap/frameworks and p3_support."""
    return st.sampled_from(CHECKPOINTS)


def report_narratives() -> st.SearchStrategy[str]:
    """ProseMirror text contract; icaap/domain/prosemirror.py treats whitespace as empty.

    Useful synthetic report sentences dominate, with blank content deliberately
    mixed in to retain the freeze machine's fail-closed readiness coverage.
    """
    return st.one_of(
        st.sampled_from(
            (
                "Capital remains adequate under the approved stress scenario.",
                "The Board requested a documented correction to the assessment.",
                "Risk appetite exceptions require an agreed management action.",
            )
        ),
        st.sampled_from((" ", "A", "Reconciled figures.")),
    )


def icaap_stages(title: str) -> list[IcaapStageInput]:
    """Minimal freeze/Board chain; icaap/domain/workflow.validate_stages shape rules."""
    return [
        IcaapStageInput(seq=1, stage_key="preparer", title=title, decision_kind="prepare"),
        IcaapStageInput(
            seq=2,
            stage_key="board",
            title="Board",
            decision_kind="approve",
            freeze_on_approve=True,
        ),
        IcaapStageInput(
            seq=3, stage_key="attestation", title="Board attestation", decision_kind="attest"
        ),
    ]


def icaap_review_histories() -> st.SearchStrategy[ReviewHistory]:
    """Preparer/CRO/approval histories before freeze; complete test framework workflow_stages."""
    return review_histories(review_decision="reviewed")
