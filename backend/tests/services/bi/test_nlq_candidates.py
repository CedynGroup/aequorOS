"""Which members the model is shown, and the two properties that must survive.

The catalogue holds 1,416 measures, so a question is answered over a subset — and a
subset is exactly where an authorization property goes quietly wrong. Two things are
asserted here and nothing else matters as much:

* **the candidate set is a SUBSET of what was passed in.** Selection can only remove.
  If it could ever add, the whole "restricted to the members the user can see" clause
  would depend on a lexical scorer;
* **an engine figure whose authority is not this institution's is not offered.** The
  same filter the certified packs apply, so a question cannot be invited about a
  figure that could only ever read as no value.

The retrieval itself is tested for the property that makes it usable — a question
that names a figure finds it, and a question that names nothing still gets the
institution's headline set — rather than for a ranking nobody should depend on.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.domain.bi.catalogue import Catalogue, MeasureDef, MemberDef, catalogue
from app.services.bi.insights import engine_measure_applies, headline_measures
from app.services.bi.nlq import candidates

BANK_CLASS = "bank"
BANK_REGIME = "crd"


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


def _select(
    cat: Catalogue, question: str, *, visible: Sequence[MemberDef] | None = None
) -> candidates.CandidateSet:
    return candidates.select(
        cat,
        visible if visible is not None else cat.members(),
        question=question,
        institution_class=BANK_CLASS,
        capital_regime=BANK_REGIME,
    )


# --- the subset property ------------------------------------------------------------------


def test_the_candidate_set_is_a_subset_of_what_was_passed_in(cat: Catalogue) -> None:
    """Selection removes. It must never be able to add."""

    visible = [m for m in cat.members() if m.module == "credit"]
    chosen = _select(cat, "gross loans by branch and product family", visible=visible)
    visible_ids = {member.id for member in visible}
    assert set(chosen.member_ids) <= visible_ids
    assert chosen.member_ids


def test_a_reader_shown_nothing_is_offered_nothing(cat: Catalogue) -> None:
    """The empty case is a REFUSAL upstream, so it has to be reachable here."""

    chosen = _select(cat, "gross loans", visible=[])
    assert chosen.empty is True
    assert chosen.member_ids == ()


def test_a_reader_with_dimensions_but_no_measures_is_still_offered_nothing(
    cat: Catalogue,
) -> None:
    """``empty`` is about FIGURES: a question needs something to measure."""

    dimensions = [m for m in cat.members() if not isinstance(m, MeasureDef)]
    chosen = _select(cat, "by branch", visible=dimensions)
    assert chosen.empty is True


# --- the engine-authority filter ----------------------------------------------------------


def test_an_engine_figure_of_another_regime_is_never_offered(cat: Catalogue) -> None:
    """A measure keyed to a regime that did not resolve for this tenant can only read
    as no value, so inviting a question about it invites a blank answer."""

    inapplicable = [
        member
        for member in cat.measures()
        if not engine_measure_applies(
            member, institution_class=BANK_CLASS, capital_regime=BANK_REGIME
        )
    ]
    assert inapplicable, "the filter must have something to exclude, or this proves nothing"
    chosen = _select(cat, inapplicable[0].label)
    offered = set(chosen.measure_ids)
    assert not offered & {member.id for member in inapplicable}


# --- retrieval: enough to be usable, and a floor under it ---------------------------------


def test_a_question_naming_a_figure_finds_that_figure(cat: Catalogue) -> None:
    target = cat.member("loans.balance_rc")
    chosen = _select(cat, f"what are our {target.label.lower()} by branch")
    assert target.id in chosen.measure_ids


def test_a_question_matching_no_label_still_gets_the_institution_s_headline_figures(
    cat: Catalogue,
) -> None:
    """Retrieval failure must not be a blank prompt: a question the scorer cannot read
    is still answered over the figures this platform considers worth a headline."""

    chosen = _select(cat, "zzzz qqqq")
    headline = {
        measure.id
        for measure in headline_measures(
            cat, institution_class=BANK_CLASS, capital_regime=BANK_REGIME
        )
    }
    assert headline
    assert headline <= set(chosen.measure_ids)


def test_the_measure_cap_is_respected(cat: Catalogue) -> None:
    chosen = candidates.select(
        cat,
        list(cat.members()),
        question="loans deposits capital liquidity balance ratio branch product",
        institution_class=BANK_CLASS,
        capital_regime=BANK_REGIME,
        measure_cap=7,
    )
    assert len(chosen.measures) == 7


def test_every_visible_dimension_is_offered(cat: Catalogue) -> None:
    """Dimensions are offered whole: a retrieval miss on a grouping is a wrong
    grouping, which is exactly the plausible-looking wrong answer to avoid."""

    visible = [m for m in cat.members() if m.sensitivity == "aggregated"]
    chosen = _select(cat, "loans", visible=visible)
    expected = {m.id for m in visible if not isinstance(m, MeasureDef)}
    assert set(chosen.dimension_ids) == expected


def test_a_drill_path_is_offered_only_when_every_level_is(cat: Catalogue) -> None:
    hierarchy = next(h for h in cat.hierarchies() if len(h.levels) > 1)
    partial = [m for m in cat.members() if m.id != hierarchy.levels[-1]]
    chosen = _select(cat, "drill down", visible=partial)
    assert hierarchy.id not in {hid for hid, _ in chosen.hierarchies}


# --- the tokeniser, because the scorer is only as honest as it ----------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What are our gross loans?", ("gross", "loans", "loan")),
        ("SHOW ME the NPL ratio", ("npl", "ratio")),
        ("a of the and", ()),
    ],
)
def test_tokenise_drops_noise_and_offers_a_singular(text: str, expected: tuple[str, ...]) -> None:
    assert candidates.tokenise(text) == expected


def test_score_is_zero_when_nothing_overlaps(cat: Catalogue) -> None:
    """The negative control for the scorer: without it the headline floor could be
    hiding a scorer that matches everything."""

    member = cat.member("loans.balance_rc")
    assert candidates.score(member, candidates.tokenise("zzzz"), "zzzz") == 0
    assert candidates.score(member, candidates.tokenise(member.label), member.label) > 0
