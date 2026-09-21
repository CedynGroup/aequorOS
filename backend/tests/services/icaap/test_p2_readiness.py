"""Readiness after P2: the assessment behind the document, not just the text.

P1 answered "is it written and are its figures current". These are the rules
that answer "is the assessment finished" — and they land in the SAME list, so a
preparer sees one checklist and P3's freeze reads one answer.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.schemas.icaap import IcaapCycleRead
from app.schemas.icaap_risk_capital import IcaapChallengeCreate, IcaapRiskPut
from app.services.icaap import challenges, readiness, readiness_p2, risks

TODAY = date(2026, 2, 1)


def _codes(db: Session, access: IcaapAccess, cycle: IcaapCycleRead) -> set[str]:
    report = readiness.get_readiness(db, access, cycle.id, today=TODAY)
    return {item.code for item in report.items}


def test_an_untouched_cycle_owes_every_p2_answer(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    codes = _codes(canonical_book, access, cycle)
    assert "risk_unassessed" in codes
    assert "requirement_reconciliation_missing" in codes
    assert "resources_reconciliation_missing" in codes
    assert "allocation_missing" in codes
    assert "audit_review_missing" in codes
    # P1's own findings are still there: one list, two authors.
    assert "section_not_committed" in codes


def test_a_material_risk_owes_a_treatment_and_an_appetite_entry(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    risks.put_risk(
        canonical_book,
        access,
        cycle.id,
        "credit",
        IcaapRiskPut(
            likelihood_score=4,
            impact_score=4,
            materiality_rationale="Concentrated book and a thin buffer.",
            reason="Score the risk.",
        ),
    )
    codes = _codes(canonical_book, access, cycle)
    assert "material_risk_without_treatment" in codes
    assert "material_risk_without_appetite" in codes


def test_a_scored_risk_no_longer_reports_itself_unassessed(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    report = readiness.get_readiness(canonical_book, access, cycle.id, today=TODAY)
    unassessed = {item.ref for item in report.items if item.code == "risk_unassessed"}
    assert "credit" in unassessed
    risks.put_risk(
        canonical_book,
        access,
        cycle.id,
        "credit",
        IcaapRiskPut(
            likelihood_score=2,
            impact_score=2,
            materiality_rationale="Small, well collateralised book.",
            reason="Score the risk.",
        ),
    )
    after = readiness.get_readiness(canonical_book, access, cycle.id, today=TODAY)
    assert "credit" not in {item.ref for item in after.items if item.code == "risk_unassessed"}


def test_a_rehearsal_softens_the_challenge_evidence_rule(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A rehearsal exists to be run before the evidence is there (D-006/D-029)."""
    report = readiness.get_readiness(canonical_book, access, cycle.id, today=TODAY)
    finding = next(item for item in report.items if item.code == "challenge_evidence_missing")
    assert finding.severity == "warning"


def test_an_open_challenge_blocks_and_answering_it_clears_the_finding(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    from app.schemas.icaap_risk_capital import IcaapChallengeResponseCreate  # noqa: PLC0415

    raised = challenges.raise_challenge(
        canonical_book,
        access,
        cycle.id,
        IcaapChallengeCreate(
            raised_in="board_risk_committee",
            raised_by_name="A. Mensah, Chair",
            raised_on=date(2026, 1, 20),
            target_kind="cycle",
            challenge_text="Is the appetite consistent with the strategy?",
            severity="medium",
        ),
    )
    assert "challenge_unanswered" in _codes(canonical_book, access, cycle)
    challenges.respond(
        canonical_book,
        access,
        cycle.id,
        raised.id,
        IcaapChallengeResponseCreate(
            outcome="accepted_no_change",
            response_text="The appetite was set against the approved strategy.",
            responder_function="Chief Risk Officer",
        ),
    )
    codes = _codes(canonical_book, access, cycle)
    assert "challenge_unanswered" not in codes
    assert "challenge_evidence_missing" not in codes


def test_every_p2_finding_is_a_sentence_and_never_a_raw_code(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    report = readiness.get_readiness(canonical_book, access, cycle.id, today=TODAY)
    for item in report.items:
        assert item.message
        assert item.message != item.code
        assert "{" not in item.message, "a template that failed to render"


def test_every_p2_rule_has_a_sentence_to_render(
    canonical_book: Session, access: IcaapAccess, cycle: IcaapCycleRead
) -> None:
    """A code with no copy would print as an enum in a Board checklist."""
    from app.services.icaap.readiness import _MESSAGES  # noqa: PLC0415

    for code in readiness_p2.readiness_codes():
        assert code in _MESSAGES


@pytest.mark.parametrize(
    ("performed", "expected"),
    [(date(2025, 1, 1), True), (date(2025, 3, 1), False)],
)
def test_a_review_older_than_the_governed_period_is_reported_as_outdated(
    performed: date, expected: bool
) -> None:
    assert readiness_p2._months_old(performed, TODAY, 12) is expected  # noqa: SLF001
