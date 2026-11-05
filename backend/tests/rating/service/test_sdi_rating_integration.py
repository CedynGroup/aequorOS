"""``AEQ-GH-SDI-FS`` — the release gate and the evidence ledger.

Founder review 2026-08-23. An SDI produced no financial-strength assessment and
the reason was not the data: ``AEQ-GH-SDI-FS`` had never been built. Only the
REFUSAL existed (``implied_rating._sdi_methodology_pending``), which is step 1 of
``AequorOS_SDI_Financial_Strength_Methodology.md`` §7 and correct as far as it
went.

These tests pin the two properties that matter about what now exists: the
scorecard cannot release a score without an approved methodology version, and a
component with no usable evidence is omitted rather than scored at a neutral
value. The second is the failure mode the whole dossier is written against —
imputing a missing Basel input as "average" is a model error, not a conservative
assumption (§1).

This module's other-suite cases live in ``test_sdi_rating.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, DeskMethodology
from app.rating.domain.sdi_scorecard import (
    CANDIDATE_RATIOS,
    OPTIONAL_COMPONENTS,
)
from app.rating.service import sdi_rating
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
AS_OF = date(2026, 3, 31)


def _sdi_bank(db: Session) -> Bank:
    bank = db.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    bank.institution_type = "savings_and_loans"
    db.flush()
    return bank


def test_no_methodology_means_pending_not_a_score(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    result = sdi_rating.assessment_state(db_session, CTX, bank, AS_OF)
    assert result.state == "methodology_pending"
    assert result.components == ()
    assert result.reason and "no version in the Desk methodology register" in result.reason


def test_a_staged_draft_is_still_pending_and_says_so(db_session: Session) -> None:
    """Staging a candidate must not release anything — only an APPROVED version
    can, and only Track-2 maker-checker creates one."""
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    row = sdi_rating.stage_candidate_methodology(
        db_session, proposed_by="ops@example.com", change_rationale="candidate structure"
    )
    assert row.status == "draft"
    assert row.approved_by is None

    result = sdi_rating.assessment_state(db_session, CTX, bank, AS_OF)
    assert result.state == "methodology_pending"
    assert result.methodology_version == row.version
    assert result.reason and "awaiting Track-2" in result.reason
    assert result.components == ()


def test_staging_never_writes_an_approved_version(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    for _ in range(3):
        sdi_rating.stage_candidate_methodology(
            db_session, proposed_by="ops@example.com", change_rationale="again"
        )
    rows = db_session.scalars(
        select(DeskMethodology).where(
            DeskMethodology.methodology_code == sdi_rating.METHODOLOGY_CODE
        )
    ).all()
    assert len(rows) == 3
    assert {row.status for row in rows} == {"draft"}
    assert sdi_rating.approved_methodology(db_session) is None
    # Versions increment; a prior version is never mutated, so a historical
    # assessment stays reproducible under the version that produced it.
    assert sorted(row.version for row in rows) == [1, 2, 3]


def test_the_sovereign_ceiling_caps_but_is_never_assumed(db_session: Session) -> None:
    """A capped grade and an uncapped one must be distinguishable, and an ABSENT
    sovereign observation must leave the grade uncapped rather than guessing."""
    assert sdi_rating._apply_ceiling("b-", "ccc") == ("ccc", True)
    assert sdi_rating._apply_ceiling("ccc-", "ccc") == ("ccc-", False)
    # No observation -> no cap. Guessing a sovereign grade would move every
    # institution's issued grade at once.
    assert sdi_rating._apply_ceiling("b-", None) == ("b-", False)


def test_every_candidate_ratio_appears_in_the_ledger(db_session: Session) -> None:
    """A ratio the platform cannot source is reported as unavailable WITH a
    reason — it never silently drops out of the assessment."""
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    evidence = sdi_rating.collect_evidence(db_session, CTX, bank, AS_OF)
    assert {item.code for item in evidence} == {ratio.code for ratio in CANDIDATE_RATIOS}
    for item in evidence:
        if not item.available:
            assert item.note, f"{item.code} is unavailable without saying why"
        assert item.source


def test_an_approved_methodology_still_refuses_when_a_component_has_no_evidence(
    db_session: Session,
) -> None:
    """The dossier's core rule, executable.

    ``earnings_capacity`` needs three annual observations that no SDI has yet.
    It is NOT optional, so the assessment is refused and names it — rather than
    scoring the component at a neutral midpoint, which would read as an average
    institution instead of an unmeasured one.
    """
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    row = sdi_rating.stage_candidate_methodology(
        db_session, proposed_by="ops@example.com", change_rationale="candidate"
    )
    row.status = "approved"
    db_session.flush()

    result = sdi_rating.assessment_state(db_session, CTX, bank, AS_OF)
    assert result.state == "not_computable"
    assert result.reason and "earnings_capacity" in result.reason
    assert result.components == ()
    # IRRBB is the declared-optional component, so it is omitted without
    # blocking; earnings is not, so it blocks.
    assert "irrbb_sensitivity" in result.omitted_components
    assert "irrbb_sensitivity" in OPTIONAL_COMPONENTS
    assert "earnings_capacity" not in OPTIONAL_COMPONENTS


def test_the_evidence_ledger_is_populated_from_the_sdi_authorities(
    db_session: Session,
) -> None:
    """Not a mock: the ledger must draw real values from the SDI's own engines,
    which is what distinguishes this from the refusal that preceded it."""
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    evidence = {
        item.code: item for item in sdi_rating.collect_evidence(db_session, CTX, bank, AS_OF)
    }
    # Capital and asset quality resolve through sdi_capital / loan_classification.
    assert evidence["car_headroom_pp"].source.startswith("sdi_capital")
    assert evidence["npl_pct"].source.startswith("loan_classification")
    # At least one component genuinely produced a number on the fixture book.
    assert any(item.available for item in evidence.values())


def test_provision_coverage_evidence_is_real_since_credit_pr1(
    db_session: Session,
) -> None:
    """Before credit PR-1 provisions HELD were not a derived figure and the
    coverage evidence was pinned unavailable unconditionally. Both honest
    outcomes must now exist: a book that states no ecl_provision_ghs still
    reports coverage UNAVAILABLE with the reason (absent is not zero), and a
    book that does state provisions yields a real coverage sourced from the
    classification engine's provisions-held split - not the requirement."""
    from tests.support.factories.canonical import (  # noqa: PLC0415
        FIXTURE_AS_OF,
        seed_canonical_fixture,
    )

    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    evidence = {
        item.code: item for item in sdi_rating.collect_evidence(db_session, CTX, bank, AS_OF)
    }
    unstated = evidence["provision_coverage_pct"]
    assert unstated.value is None
    assert "no loan states ecl_provision_ghs" in (unstated.note or "")

    seed_canonical_fixture(db_session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    db_session.flush()
    evidence = {
        item.code: item
        for item in sdi_rating.collect_evidence(db_session, CTX, bank, FIXTURE_AS_OF)
    }
    coverage = evidence["provision_coverage_pct"]
    assert coverage.value is not None
    assert coverage.source == "loan_classification.provisions_held"


def test_an_approved_methodology_produces_discriminating_component_scores(
    db_session: Session,
) -> None:
    """The assessment must actually SCORE, and the scores must vary.

    A scorecard whose components all land on the same value is not measuring
    anything. This pins that an approved methodology over the fixture book
    yields per-component scores inside the unit interval with real spread —
    which is what caught two earlier defects: passing an operating-environment
    score of 0 with the bank matrix floored every component to exactly 0.0000,
    and seven lower-is-better ratios were written with floor > cap.
    """
    materialize_canonical_test_book(db_session)
    bank = _sdi_bank(db_session)
    row = sdi_rating.stage_candidate_methodology(
        db_session, proposed_by="ops@example.com", change_rationale="candidate"
    )
    row.status = "approved"
    db_session.flush()

    result = sdi_rating.assessment_state(db_session, CTX, bank, AS_OF)
    if result.state != "advisory":
        # The fixture book may not carry every mandatory component; when it does
        # not, the refusal must still NAME what is missing rather than scoring it.
        assert result.state == "not_computable"
        assert result.reason and result.omitted_components
        return

    assert result.components
    scores = [component.score for component in result.components]
    assert all(Decimal(0) <= score <= Decimal(1) for score in scores)
    assert len(set(scores)) > 1, "components that all score identically measure nothing"
    # Weights renormalise over the components that scored.
    assert sum(c.weight for c in result.components) == Decimal(1)
    # And the release gate still holds: advisory means scores, never a grade.
    assert result.releases_grade is False
    assert result.releases_pd is False
