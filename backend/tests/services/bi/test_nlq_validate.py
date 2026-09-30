"""The refusal boundary. Every test here constructs a BAD model output on purpose.

This is the file the brief's warning is about: "a query that runs and returns a
plausible number the user did not ask for is the worst outcome available here". The
only thing standing between a model's answer and a compiled query is
``nlq.translate``, so every way a model can be wrong is written out here as a draft
and asserted to produce a refusal — not a narrowed query, not a dropped clause, not a
substituted member.

Two properties get their own tests because they are the ones an audit would look for:

* **a refusal names nothing the caller was not entitled to know exists.** The three
  internal facts behind ``unrecognised_member`` produce ONE code and ONE message, and
  the offending ids are carried separately for the queue row rather than for a reader;
* **no date in a query came from the model.** The draft schema has no date field at
  all, so the test asserts the computed window against the reader's own ``as_of``, and
  a separate test proves the schema would reject a date if one were added.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.bi.catalogue import Catalogue, catalogue
from app.schemas.bi import shift_months
from app.services.bi import nlq
from app.services.bi.nlq.schema import (
    NlqDraft,
    NlqFilterDraft,
    NlqQueryDraft,
    NlqSortDraft,
    NlqTimeDraft,
    NlqTopNDraft,
)
from app.services.bi.nlq.validate import Translation

AS_OF = dt.date(2026, 8, 31)
MEASURE = "loans.balance_rc"
DIMENSION = "branch.code"
#: A real catalogue member that ``MEASURE`` cannot be sliced by: it belongs to the
#: loan-event fact, not the position fact ``loans.balance_rc`` reads.
UNSLICEABLE = "event.type"
#: A real, whole-institution figure: correct as an id, wrong as something to group.
INSTITUTION_MEASURE = "engine.car_pct.crd.official"
OFFERED = frozenset({MEASURE, DIMENSION, UNSLICEABLE, INSTITUTION_MEASURE})


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


def _draft(**query: Any) -> NlqDraft:
    """An answerable draft over the offered members, overridable field by field."""

    defaults: dict[str, Any] = {"measures": [MEASURE], "time": NlqTimeDraft()}
    return NlqDraft(answerable=True, query=NlqQueryDraft(**{**defaults, **query}))


def _translate(
    cat: Catalogue, draft: NlqDraft, *, offered: frozenset[str] = OFFERED
) -> Translation:
    return nlq.translate(cat, draft, offered_member_ids=offered, as_of=AS_OF)


# --- the happy path, so the refusals below mean something --------------------------------


def test_an_offered_query_becomes_a_bi_query(cat: Catalogue) -> None:
    """The control. Without it every refusal test could pass vacuously."""

    outcome = _translate(cat, _draft(dimensions=[DIMENSION]))
    assert outcome.refusal is None
    query = outcome.query
    assert query is not None
    assert query.measures == [MEASURE]
    assert query.dimensions == [DIMENSION]
    assert query.time.as_of == AS_OF
    assert query.time.compare_to is None


# --- restricted to the members the user can see -------------------------------------------


@pytest.mark.parametrize(
    "query_kwargs",
    [
        pytest.param({"measures": ["loans.balance_rc", "deposits.balance_rc"]}, id="measure"),
        pytest.param({"dimensions": ["counterparty.name"]}, id="dimension"),
        pytest.param(
            {"filters": [NlqFilterDraft(member="product.family", op="eq", values=["MORTGAGE"])]},
            id="filter",
        ),
        pytest.param(
            {"dimensions": [DIMENSION], "top_n": NlqTopNDraft(dimension="product.family", n=5)},
            id="top_n",
        ),
        pytest.param(
            {"sort": [NlqSortDraft(member="deposits.balance_rc", direction="desc")]}, id="sort"
        ),
    ],
)
def test_a_member_that_was_not_offered_is_refused_wherever_it_appears(
    cat: Catalogue, query_kwargs: dict[str, Any]
) -> None:
    """Every position a member id can occupy is walked, not just ``measures``.

    A filter reveals as surely as a projection does (``services/bi/authorization``
    rule 1), so an unoffered id in a filter, a Top-N axis or a sort key has to refuse
    exactly as an unoffered measure does.
    """

    outcome = _translate(cat, _draft(**query_kwargs))
    assert outcome.query is None
    assert outcome.refusal == "unrecognised_member"


def test_the_refusal_is_byte_identical_for_absent_hidden_and_unoffered_ids(cat: Catalogue) -> None:
    """The disclosure test: the reader must not learn WHICH of the three it was.

    ``loans.balance_rc`` exists and is offered here; ``counterparty.name`` exists and
    is not offered (a reader's grants hide it, or this question did not retrieve it);
    ``no.such.figure`` does not exist at all. All three must answer the same, or the
    refusal becomes an oracle for the existence of figures a reader may not see.
    """

    hidden = _translate(cat, _draft(measures=["counterparty.name"]))
    absent = _translate(cat, _draft(measures=["no.such.figure"]))
    unoffered = _translate(
        cat, _draft(measures=[MEASURE]), offered=frozenset({"deposits.balance_rc"})
    )
    codes = {o.refusal for o in (hidden, absent, unoffered)}
    messages = {o.message for o in (hidden, absent, unoffered)}
    assert codes == {"unrecognised_member"}
    assert len(messages) == 1
    assert not any(
        token in next(iter(messages))
        for token in ("counterparty", "no.such.figure", "loans.balance_rc")
    )


def test_the_offending_ids_are_carried_for_the_queue_row_and_not_for_the_reader(
    cat: Catalogue,
) -> None:
    """A model naming a member this reader may not see is what a reviewer looks for.

    So it is recorded — on ``unoffered_members``, which the job writes to its progress
    record — while ``message`` stays the one generic sentence.
    """

    outcome = _translate(cat, _draft(measures=["counterparty.name"]))
    assert outcome.unoffered_members == ("counterparty.name",)
    assert "counterparty.name" not in outcome.message


def test_a_suggestion_outside_the_offered_set_is_dropped(cat: Catalogue) -> None:
    """Suggestions are ids, and an id the reader was not offered is not a suggestion."""

    draft = NlqDraft(
        answerable=False,
        unanswerable_reason="no_matching_figure",
        suggested_members=[MEASURE, "counterparty.name", "no.such.figure"],
    )
    outcome = _translate(cat, draft)
    assert outcome.suggested_members == (MEASURE,)


# --- malformed, or not a BiQuery at all ---------------------------------------------------


def test_a_draft_that_is_not_a_bi_query_at_all_is_refused() -> None:
    """The model's output type is closed: an extra key, or SQL in place of a query,
    fails at the schema and never reaches ``translate``."""

    with pytest.raises(ValidationError):
        NlqDraft.model_validate(
            {"answerable": True, "query": {"sql": "select * from bi_fact_position_daily"}}
        )
    with pytest.raises(ValidationError):
        NlqDraft.model_validate(
            {
                "answerable": True,
                "query": {"measures": [MEASURE], "time": {}, "raw_sql": "select 1"},
            }
        )


def test_the_draft_schema_has_no_date_field_anywhere() -> None:
    """The structural reason a model can never name a reporting date."""

    with pytest.raises(ValidationError):
        NlqTimeDraft.model_validate({"window": "as_of", "as_of": "2020-01-01"})
    fields = set(NlqTimeDraft.model_fields)
    assert fields == {"window", "trailing_months", "compare_with_prior"}


@pytest.mark.parametrize(
    "time",
    [
        pytest.param(NlqTimeDraft(window="as_of", trailing_months=6), id="months_on_single_date"),
        pytest.param(NlqTimeDraft(window="trailing_range"), id="range_without_length"),
    ],
)
def test_an_incoherent_period_intent_is_refused(cat: Catalogue, time: NlqTimeDraft) -> None:
    outcome = _translate(cat, _draft(time=time))
    assert outcome.refusal == "malformed_output"


def test_a_contradictory_draft_is_refused_rather_than_interpreted(cat: Catalogue) -> None:
    """Both halves, because a model that fills neither is as wrong as one that
    fills both, and guessing which it meant is how a reader gets an answer to a
    question nobody asked."""

    both = NlqDraft(answerable=False, query=NlqQueryDraft(measures=[MEASURE]))
    neither = NlqDraft(answerable=True)
    assert _translate(cat, both).refusal == "contradictory_output"
    assert _translate(cat, neither).refusal == "contradictory_output"


# --- structurally valid ids, but a query the compiler would refuse ------------------------


def test_a_grouping_the_figure_does_not_allow_is_refused_before_the_reader_confirms(
    cat: Catalogue,
) -> None:
    """``loans.balance_rc`` cannot be sliced by ``event.type``. The compiler would
    refuse it; refusing here means a reader is never asked to confirm a query that
    cannot run."""

    outcome = _translate(cat, _draft(dimensions=[UNSLICEABLE]))
    assert outcome.refusal == "unusable_query"


def test_a_whole_institution_figure_cannot_be_grouped(cat: Catalogue) -> None:
    """A capital ratio over one branch is a wrong number wearing a right name."""

    outcome = _translate(cat, _draft(measures=[INSTITUTION_MEASURE], dimensions=[DIMENSION]))
    assert outcome.refusal == "unusable_query"


def test_a_top_n_over_a_dimension_the_query_does_not_group_by_is_refused(cat: Catalogue) -> None:
    outcome = _translate(cat, _draft(top_n=NlqTopNDraft(dimension=DIMENSION, n=10)))
    assert outcome.refusal == "unusable_query"


def test_a_sort_on_something_the_query_does_not_return_is_refused(cat: Catalogue) -> None:
    outcome = _translate(cat, _draft(sort=[NlqSortDraft(member=DIMENSION, direction="desc")]))
    assert outcome.refusal == "unusable_query"


def test_a_filter_with_the_wrong_number_of_values_is_refused(cat: Catalogue) -> None:
    """``BiQuery``'s own operator arity is the check; the draft's looser types are
    what let the failure happen here instead of at compile time."""

    outcome = _translate(
        cat,
        _draft(filters=[NlqFilterDraft(member=DIMENSION, op="between", values=["A"])]),
    )
    assert outcome.refusal == "malformed_output"


# --- the platform computes every date -----------------------------------------------------


def test_a_prior_period_comparison_is_the_platform_s_own_prior_date(cat: Catalogue) -> None:
    outcome = _translate(cat, _draft(time=NlqTimeDraft(compare_with_prior=True)))
    query = outcome.query
    assert query is not None
    assert query.time.as_of == AS_OF
    assert query.time.compare_to == shift_months(AS_OF, -1)


def test_a_trailing_window_ends_at_the_reader_s_date_and_starts_n_months_before(
    cat: Catalogue,
) -> None:
    outcome = _translate(
        cat,
        _draft(time=NlqTimeDraft(window="trailing_range", trailing_months=6)),
    )
    query = outcome.query
    assert query is not None
    assert query.time.range is not None
    assert query.time.range.end == AS_OF
    assert query.time.range.start == shift_months(AS_OF, -5)


def test_a_trailing_window_comparison_is_an_equal_length_prior_window(cat: Catalogue) -> None:
    outcome = _translate(
        cat,
        _draft(
            time=NlqTimeDraft(window="trailing_range", trailing_months=3, compare_with_prior=True)
        ),
    )
    query = outcome.query
    assert query is not None
    assert query.time.range is not None
    assert query.time.compare_to == shift_months(query.time.range.start, -1)


def test_an_unanswerable_reason_is_rendered_as_the_platform_s_own_sentence(cat: Catalogue) -> None:
    """The model's code, never the model's words."""

    for reason, message in nlq.UNANSWERABLE_MESSAGES.items():
        draft = NlqDraft(answerable=False, unanswerable_reason=reason)  # type: ignore[arg-type]
        outcome = _translate(cat, draft)
        assert outcome.refusal == "unanswerable"
        assert outcome.message == message
