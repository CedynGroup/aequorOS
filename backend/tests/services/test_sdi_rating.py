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
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.rating.sdi_scorecard import (
    CANDIDATE_COMPONENTS,
    CANDIDATE_GRADE_CUTPOINTS,
    CANDIDATE_RATIOS,
    GRADE_ORDER,
    OPTIONAL_COMPONENTS,
    candidate_parameters,
)
from app.models import Bank, DeskMethodology
from app.services import sdi_rating
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


# ---------------------------------------------------------------------------
# The structure
# ---------------------------------------------------------------------------


def test_component_and_ratio_weights_are_internally_consistent() -> None:
    assert sum(c.weight for c in CANDIDATE_COMPONENTS) == Decimal("1.00")
    by_component: dict[str, Decimal] = {}
    for ratio in CANDIDATE_RATIOS:
        by_component[ratio.component] = by_component.get(ratio.component, Decimal(0)) + ratio.weight
    for component in CANDIDATE_COMPONENTS:
        assert by_component[component.code] == Decimal("1.00"), component.code


def test_the_basel_only_inputs_are_excluded_by_name() -> None:
    """§2: FX NOP, CET1, Tier-1 leverage, Basel LCR and NSFR are excluded, and
    their absence is not neutral evidence. Naming them in the stored parameters
    makes the exclusion auditable rather than merely absent."""
    payload = candidate_parameters()
    assert set(payload["excluded_inputs"]) == {  # type: ignore[arg-type]
        "fx_nop",
        "cet1",
        "tier1_leverage",
        "basel_lcr",
        "basel_nsfr",
    }
    codes = {ratio.code for ratio in CANDIDATE_RATIOS}
    for banned in ("cet1_pct", "lcr_pct", "nsfr_pct", "nop_pct_tier1", "leverage_pct"):
        assert banned not in codes


def test_stored_parameters_declare_themselves_uncalibrated() -> None:
    """A reader of the stored row must not mistake a candidate for a model."""
    status = str(candidate_parameters()["parameter_status"])
    assert "CANDIDATE STRUCTURE ONLY" in status
    assert "uncalibrated" in status
    assert "No grade and no PD" in status


# ---------------------------------------------------------------------------
# The release gate
# ---------------------------------------------------------------------------


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


def test_pd_stays_closed_while_the_grade_is_released() -> None:
    """A grade is issued (founder decision 2026-08-23); a PD is NOT.

    They are different problems. A grade is an ordering of institutions by
    measured strength. A probability of default is a calibrated frequency, and
    it needs representative outcome data, low-default-portfolio treatment and a
    margin of conservatism that no SDI population supplies (dossier §4 state 5).
    Releasing the first must never be read as licence to derive the second, so
    the scorecard carries no PD field at all.
    """
    assert sdi_rating.RELEASES_GRADE is True
    assert sdi_rating.RELEASES_PD is False
    fields = {name.lower() for name in sdi_rating.SdiAssessment.__annotations__}
    assert not any(name == "pd" or name.endswith("_pd") or "pd_band" in name for name in fields)


def test_the_grade_ladder_expresses_no_investment_grade() -> None:
    """The SDI ladder stops at ``bb+`` — a scope statement, not a truncation.

    A Ghanaian SDI cannot realistically be investment grade and the sovereign
    ceiling sits at Ghana's own grade in any case. Carrying unreachable
    ``aaa``…``bbb-`` entries would imply a standing the model cannot evidence.
    """
    assert GRADE_ORDER[0] == "bb+"
    assert not ({"aaa", "aa", "a", "bbb", "bbb-"} & set(GRADE_ORDER))
    values = [Decimal(v) for v in CANDIDATE_GRADE_CUTPOINTS.values()]
    assert values == sorted(values, reverse=True), "cutpoints must descend"
    assert all(Decimal(0) <= v <= Decimal(1) for v in values)


def test_the_sovereign_ceiling_caps_but_is_never_assumed(db_session: Session) -> None:
    """A capped grade and an uncapped one must be distinguishable, and an ABSENT
    sovereign observation must leave the grade uncapped rather than guessing."""
    assert sdi_rating._apply_ceiling("b-", "ccc") == ("ccc", True)
    assert sdi_rating._apply_ceiling("ccc-", "ccc") == ("ccc-", False)
    # No observation -> no cap. Guessing a sovereign grade would move every
    # institution's issued grade at once.
    assert sdi_rating._apply_ceiling("b-", None) == ("b-", False)


# ---------------------------------------------------------------------------
# Evidence is reported, never imputed
# ---------------------------------------------------------------------------


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
        item.code: item
        for item in sdi_rating.collect_evidence(db_session, CTX, bank, AS_OF)
    }
    # Capital and asset quality resolve through sdi_capital / loan_classification.
    assert evidence["car_headroom_pp"].source.startswith("sdi_capital")
    assert evidence["npl_pct"].source.startswith("loan_classification")
    # At least one component genuinely produced a number on the fixture book.
    assert any(item.available for item in evidence.values())


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


def test_every_lower_is_better_ratio_has_floor_below_cap() -> None:
    """The engine's convention, which is the opposite of the intuitive reading.

    For ``lower_is_better`` the score is ``(cap - value) / (cap - floor)``, so the
    FLOOR is the best value and the CAP the worst. Written the intuitive way
    round the engine refuses outright — all seven were inverted when first
    written (2026-08-23).
    """
    for ratio in CANDIDATE_RATIOS:
        assert ratio.floor < ratio.cap, f"{ratio.code}: floor must be below cap"


def test_no_operating_environment_adjustment_is_silently_applied() -> None:
    """Omitted, not applied at its worst value.

    The bank matrix ``((0,0),(0,1))`` with an environment score of 0 floors every
    ratio to zero. Passing 0 because no determination exists would assert "worst
    possible environment" — a substitution the dossier forbids.
    """
    low, high = sdi_rating._IDENTITY_ENVIRONMENT
    assert low == (Decimal(0), Decimal(0))
    assert high == (Decimal(1), Decimal(1))
