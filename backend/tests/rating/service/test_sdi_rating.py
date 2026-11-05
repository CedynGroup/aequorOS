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

This module's other-suite cases live in ``test_sdi_rating_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

from app.rating.domain.sdi_scorecard import (
    CANDIDATE_COMPONENTS,
    CANDIDATE_GRADE_CUTPOINTS,
    CANDIDATE_RATIOS,
    GRADE_ORDER,
    candidate_parameters,
)
from app.rating.service import sdi_rating


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
