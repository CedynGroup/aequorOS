"""The calculated-measure formula language, tested as an attacker reads it.

Four things are worth breaking here, in order of what it would cost:

1. **:func:`referenced_members` under-reporting.** It is the list a calculated
   measure is authorized against. An id it omits is a figure read without a
   binding, which is the whole of the BI authorization model defeated by a
   formula. That is why completeness is a hypothesis PROPERTY over generated
   formulas rather than a handful of examples — examples only prove the shapes
   somebody thought of.
2. **Anything the parser treats as special.** Quotes, semicolons, comment
   markers, backticks, null bytes and script lookalikes must be refused or be
   ordinary, never significant.
3. **The bounds.** A formula has to be refused BY NAME when it is too long, too
   deep or names too many figures — never by the interpreter running out of
   stack, and never by hanging.
4. **What comes back out.** A refusal may not hand the caller's own text back
   unchecked.
"""

from __future__ import annotations

import ast as python_ast
import re
import time
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from hypothesis import HealthCheck, assume, given, reject, settings
from hypothesis import strategies as st

from app.domain.bi import expr as expr_module
from app.domain.bi.expr import (
    ALPHABET,
    FUNCTION_NAMES,
    GRAIN_MAX_DAYS,
    GRAIN_WORDS,
    MAX_DEPTH,
    MAX_EXCERPT_LENGTH,
    MAX_EXPRESSION_LENGTH,
    MAX_LAG_PERIODS,
    MAX_MEMBER_REFERENCES,
    REDACTION,
    Arithmetic,
    Comparison,
    Conditional,
    DivisionByZero,
    EmptyExpression,
    Expr,
    ExpressionError,
    ExpressionTooDeep,
    ExpressionTooLong,
    Lag,
    LagBeyondRetention,
    Logical,
    LogicalNot,
    MalformedMemberReference,
    MemberReference,
    MixedPeriodGrains,
    Negation,
    NonNumericExpression,
    NumberLiteral,
    PercentChange,
    PeriodGrain,
    SafeDivide,
    TooManyMemberReferences,
    TrailingContent,
    UnbalancedParenthesis,
    UnexpectedCharacter,
    UnexpectedEndOfExpression,
    UnexpectedToken,
    UnknownFunction,
    UnknownPeriodGrain,
    WrongArgumentCount,
    WrongArgumentType,
    declared_grain,
    depth,
    max_lag_periods,
    period_offsets,
    referenced_members,
    walk,
)
from app.schemas.bi import BI_MAX_MEASURES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _typeshed import DataclassInstance

#: The retention window these tests parse against unless they say otherwise. A
#: deployment default of 95 days allows a three-month reach, which is too short to
#: write the lag bounds down; 400 days is a realistic long-retention deployment
#: (``tests/test_config.py`` uses the same number) and gives twelve months, four
#: quarters and one year.
RETENTION_DAYS = 400


def parse(source: str, *, retention_days: int | None = RETENTION_DAYS) -> Expr:
    """``expr.parse`` with a retention window supplied.

    The real signature REQUIRES ``retention_days`` and has no default (D-196): the
    write path passes the deployment's window and an authorization walk passes
    ``None``, and neither may get its behaviour by forgetting to say which. This
    shim supplies one so the hundred tests that are about something else do not
    each restate it; ``test_parse_will_not_guess_a_retention_window`` pins that the
    real function still refuses to be called without it.
    """
    return expr_module.parse(source, retention_days=retention_days)


#: How a figure reference is spotted in RAW SOURCE, independently of the parser.
#: Deliberately a second implementation: a completeness property checked with
#: the parser's own regex would prove only that the parser agrees with itself.
FIGURE_IN_SOURCE = re.compile(r"\[m:([a-z0-9_]+(?:\.[a-z0-9_]+)*)\]")

#: Every node kind a formula can produce. Pinned as a list so a node added
#: without a test showing how it is written fails the coverage check below.
NODE_KINDS = (
    NumberLiteral,
    MemberReference,
    Negation,
    Arithmetic,
    Comparison,
    Logical,
    LogicalNot,
    SafeDivide,
    Conditional,
    PercentChange,
    Lag,
)

#: One formula that reaches every node kind, with a different figure in each
#: position so the order :func:`referenced_members` reports can be read off it.
KITCHEN_SINK = (
    "IF(PCT_CHANGE([m:a], MONTH) > SAFE_DIV([m:b], LAG([m:c], 2, MONTH)) "
    "AND NOT [m:d] < -[m:e], [m:f] * 2, [m:g] + 1)"
)

#: Characters no message may contain, whatever the caller sent. Quotes, comment
#: and statement punctuation, and control bytes: the things that are dangerous
#: precisely because some downstream surface treats them as structure.
FORBIDDEN_IN_MESSAGES = "'\"`;\\\x00\n\r\t"


def _shape(node: Expr) -> object:
    """The tree without its offsets.

    Node equality includes ``position``, which is right — two writings of the
    same formula are not the same text — but it makes "spacing and case do not
    change the meaning" unstatable. This drops the offsets and keeps everything
    else, so a test that means "the same tree" can say so.
    """
    scalars = tuple(
        (spec.name, getattr(node, spec.name))
        for spec in fields(cast("DataclassInstance", node))
        if spec.name != "position" and not isinstance(getattr(node, spec.name), Expr)
    )
    return (type(node).__name__, scalars, tuple(_shape(child) for child in node.children))


def _refuse(source: str) -> ExpressionError:
    """Parse ``source``, require a named refusal, and return it."""
    return _refuse_with(source, retention_days=RETENTION_DAYS)


def _refuse_with(source: str, *, retention_days: int | None) -> ExpressionError:
    """The same, against a stated retention window."""
    with pytest.raises(ExpressionError) as caught:
        parse(source, retention_days=retention_days)
    error = caught.value
    _assert_posture(error, source)
    return error


def _assert_posture(error: ExpressionError, source: str) -> None:
    """Every refusal, whatever it is, keeps the same promises."""
    assert type(error) is not ExpressionError, "a refusal must name itself, not be the base class"
    assert error.code and error.code.replace("_", "").isalnum()
    assert 0 <= error.position <= len(source)
    assert error.message.strip()
    assert not set(error.message) & set(FORBIDDEN_IN_MESSAGES), error.message
    assert len(error.excerpt) <= MAX_EXCERPT_LENGTH
    assert set(error.excerpt) <= ALPHABET | {REDACTION}, error.excerpt


# --- precedence and associativity --------------------------------------------------------------


def test_multiplication_binds_tighter_than_addition() -> None:
    tree = parse("1 + 2 * 3")
    assert isinstance(tree, Arithmetic)
    assert tree.operator == "+"
    assert isinstance(tree.left, NumberLiteral)
    assert isinstance(tree.right, Arithmetic)
    assert tree.right.operator == "*"


def test_brackets_override_precedence() -> None:
    tree = parse("(1 + 2) * 3")
    assert isinstance(tree, Arithmetic)
    assert tree.operator == "*"
    assert isinstance(tree.left, Arithmetic)
    assert tree.left.operator == "+"


@pytest.mark.parametrize("operator", ["-", "/"])
def test_subtraction_and_division_are_left_associative(operator: str) -> None:
    """``a - b - c`` is ``(a - b) - c``. Right association would change the value."""
    tree = parse(f"[m:a] {operator} [m:b] {operator} [m:c]")
    assert isinstance(tree, Arithmetic)
    assert isinstance(tree.left, Arithmetic)
    assert isinstance(tree.right, MemberReference)
    assert tree.right.member_id == "c"
    assert isinstance(tree.left.left, MemberReference)
    assert tree.left.left.member_id == "a"


def test_unary_minus_binds_tighter_than_multiplication() -> None:
    tree = parse("-[m:a] * [m:b]")
    assert isinstance(tree, Arithmetic)
    assert tree.operator == "*"
    assert isinstance(tree.left, Negation)


def test_unary_minus_applies_to_a_bracketed_expression() -> None:
    tree = parse("-([m:a] + [m:b])")
    assert isinstance(tree, Negation)
    assert isinstance(tree.operand, Arithmetic)


def test_unary_minus_stacks() -> None:
    tree = parse("- -[m:a]")
    assert isinstance(tree, Negation)
    assert isinstance(tree.operand, Negation)


def test_brackets_add_no_node_of_their_own() -> None:
    """``((([m:a])))`` is the same tree as ``[m:a]`` — nesting is not structure."""
    assert _shape(parse("((([m:a])))")) == _shape(parse("[m:a]"))
    assert depth(parse("((([m:a])))")) == 1


def test_not_binds_tighter_than_and_and_looser_than_a_comparison() -> None:
    """``NOT a > b AND c > d`` is ``(NOT (a > b)) AND (c > d)``."""
    tree = parse("IF(NOT [m:a] > [m:b] AND [m:c] > [m:d], 1, 0)")
    assert isinstance(tree, Conditional)
    condition = tree.condition
    assert isinstance(condition, Logical)
    assert condition.operator == "and"
    assert isinstance(condition.left, LogicalNot)
    assert isinstance(condition.left.operand, Comparison)
    assert isinstance(condition.right, Comparison)


def test_and_binds_tighter_than_or() -> None:
    tree = parse("IF([m:a] > 1 OR [m:b] > 1 AND [m:c] > 1, 1, 0)")
    assert isinstance(tree, Conditional)
    condition = tree.condition
    assert isinstance(condition, Logical)
    assert condition.operator == "or"
    assert isinstance(condition.right, Logical)
    assert condition.right.operator == "and"


def test_comparisons_cannot_be_chained() -> None:
    """``1 < 2 < 3`` reads as maths and means nothing here; say so rather than
    silently comparing a yes/no answer with a number."""
    error = _refuse("IF(1 < 2 < 3, 1, 0)")
    assert isinstance(error, UnexpectedToken)
    assert "AND" in error.message


@pytest.mark.parametrize(
    ("source", "value"),
    [("1", Decimal(1)), ("1.5", Decimal("1.5")), ("0.25", Decimal("0.25")), ("000", Decimal(0))],
)
def test_numbers_are_kept_exactly(source: str, value: Decimal) -> None:
    """Decimal, not float: a calculated measure is money and ratios."""
    tree = parse(source)
    assert isinstance(tree, NumberLiteral)
    assert tree.value == value


def test_whitespace_between_tokens_is_insignificant() -> None:
    assert _shape(parse("[m:a]+[m:b]")) == _shape(parse("  [m:a]\t+\n[m:b]  "))


# --- functions ----------------------------------------------------------------------------------


def test_every_function_parses_to_its_own_node() -> None:
    assert isinstance(parse("SAFE_DIV([m:a], [m:b])"), SafeDivide)
    assert isinstance(parse("IF([m:a] > 1, 1, 0)"), Conditional)
    assert isinstance(parse("PCT_CHANGE([m:a], MONTH)"), PercentChange)
    assert isinstance(parse("LAG([m:a], 3, QUARTER)"), Lag)


def test_function_and_keyword_names_are_case_insensitive() -> None:
    """A formula editor cannot make people shout."""
    assert _shape(parse("safe_div([m:a], [m:b])")) == _shape(parse("SAFE_DIV([m:a], [m:b])"))
    assert _shape(parse("if ([m:a] > 1 and not [m:b] > 1, 1, 0)")) == _shape(
        parse("IF([m:a] > 1 AND NOT [m:b] > 1, 1, 0)")
    )


@pytest.mark.parametrize(
    ("source", "expected", "given"),
    [
        ("SAFE_DIV([m:a])", 2, 1),
        ("SAFE_DIV([m:a], [m:b], [m:c])", 2, 3),
        ("IF([m:a] > 1, 1)", 3, 2),
        ("PCT_CHANGE([m:a], MONTH, MONTH)", 2, 3),
        ("LAG([m:a], 1, MONTH, MONTH)", 3, 4),
    ],
)
def test_a_function_called_with_the_wrong_number_of_inputs_is_named(
    source: str, expected: int, given: int
) -> None:
    error = _refuse(source)
    assert isinstance(error, WrongArgumentCount)
    assert str(expected) in error.message
    assert str(given) in error.message


def test_a_call_with_far_too_many_inputs_is_refused_before_they_are_all_built() -> None:
    error = _refuse("IF(1, 2, 3, 4, 5, 6, 7, 8, 9)")
    assert isinstance(error, WrongArgumentCount)


@pytest.mark.parametrize(
    "source",
    [
        "IF(1, 2, 3)",
        "IF([m:a], 1, 0)",
        "IF([m:a] > 1, [m:b] > 1, 0)",
        "IF([m:a] > 1, 0, [m:b] > 1)",
    ],
)
def test_if_requires_a_condition_and_two_numbers(source: str) -> None:
    error = _refuse(source)
    assert isinstance(error, WrongArgumentType)


def test_a_comparison_cannot_be_used_as_a_number() -> None:
    for source in (
        "SAFE_DIV([m:a] > 1, [m:b])",
        "PCT_CHANGE([m:a] > 1, MONTH)",
        "LAG([m:a] > 1, 1, MONTH)",
    ):
        assert isinstance(_refuse(source), WrongArgumentType)


def test_a_number_cannot_be_used_as_a_condition() -> None:
    for source in ("IF(NOT 1, 1, 0)", "IF(1 AND 2, 1, 0)", "IF([m:a] OR 1, 1, 0)"):
        assert isinstance(_refuse(source), WrongArgumentType)


@pytest.mark.parametrize(
    "periods",
    [
        "1.5",
        "-1",
        "0",
        str(MAX_LAG_PERIODS + 1),
        "[m:b]",
        "1 + 1",
        "PCT_CHANGE([m:b], MONTH)",
    ],
)
def test_lag_takes_a_whole_number_of_periods_written_directly(periods: str) -> None:
    """A lag decides how far back the query window is widened, so it cannot be a
    per-row value, a fraction, or a look FORWARD spelled as a negative."""
    error = _refuse(f"LAG([m:a], {periods}, MONTH)")
    assert isinstance(error, WrongArgumentType)
    assert str(MAX_LAG_PERIODS) in error.message


def test_lag_accepts_its_bounds() -> None:
    """The LANGUAGE's ceiling, with no retention window applied.

    ``MAX_LAG_PERIODS`` is structural — it keeps the tree finite — and is
    deliberately not the bound a deployment answers to; that one is
    :func:`max_lag_periods` and has its own tests below. Parsing with
    ``retention_days=None`` is what separates the two.
    """
    assert parse("LAG([m:a], 1, MONTH)", retention_days=None).periods == 1  # type: ignore[attr-defined]
    ceiling = parse(f"LAG([m:a], {MAX_LAG_PERIODS}, MONTH)", retention_days=None)
    assert ceiling.periods == MAX_LAG_PERIODS  # type: ignore[attr-defined]
    assert parse("LAG([m:a], 2.0, MONTH)", retention_days=None).periods == 2  # type: ignore[attr-defined]


def test_an_unknown_function_names_the_ones_that_exist() -> None:
    error = _refuse("TOTAL([m:a])")
    assert isinstance(error, UnknownFunction)
    assert "TOTAL" in error.message
    for name in FUNCTION_NAMES:
        assert name in error.message


def test_an_absurdly_long_function_name_is_not_repeated_back() -> None:
    error = _refuse("a" * 200 + "(1)")
    assert isinstance(error, UnknownFunction)
    assert "aaaa" not in error.message


def test_a_function_name_without_brackets_says_so() -> None:
    error = _refuse("IF")
    assert isinstance(error, UnexpectedToken)
    assert "bracket" in error.message


def test_a_keyword_is_not_a_function() -> None:
    assert isinstance(_refuse("AND(1, 2)"), UnexpectedToken)


# --- division -----------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["[m:a] / 0", "[m:a] / 0.0", "[m:a] / -0", "[m:a] / (-(-0))"])
def test_dividing_by_a_written_zero_is_refused_and_points_at_safe_div(source: str) -> None:
    error = _refuse(source)
    assert isinstance(error, DivisionByZero)
    assert "SAFE_DIV" in error.message


def test_safe_div_by_a_written_zero_is_allowed() -> None:
    """SAFE_DIV is DEFINED to give no value when the bottom is zero, so a zero
    there is pointless but not wrong. Refusing it would contradict the function."""
    assert isinstance(parse("SAFE_DIV([m:a], 0)"), SafeDivide)


def test_a_zero_that_is_only_zero_at_run_time_is_not_caught_here() -> None:
    """The check is deliberately literal-only. Proving ``[m:a] - [m:a]`` is zero
    is evaluation, and this module evaluates nothing; SAFE_DIV is the answer for
    a divisor whose value is not known until the query runs."""
    assert isinstance(parse("[m:a] / ([m:b] - [m:b])"), Arithmetic)


# --- what the formula as a whole has to be -------------------------------------------------------


@pytest.mark.parametrize("source", ["[m:a] > [m:b]", "NOT [m:a] > 1", "[m:a] > 1 AND [m:b] > 1"])
def test_a_formula_that_works_out_to_a_yes_or_no_is_not_a_measure(source: str) -> None:
    error = _refuse(source)
    assert isinstance(error, NonNumericExpression)
    assert "IF" in error.message


@pytest.mark.parametrize("source", ["", "   ", "\t\n"])
def test_an_empty_formula_is_named(source: str) -> None:
    assert isinstance(_refuse(source), EmptyExpression)


# --- the declared period grain (D-195) ----------------------------------------------------------


@pytest.mark.parametrize("word", ["MONTH", "QUARTER", "YEAR"])
def test_a_period_comparison_declares_its_grain_in_the_text(word: str) -> None:
    """The grain is in the FORMULA, which is what a checker reads and approves.

    D-195: the alternative is inheriting it from the query, and then one certified
    label renders month-on-month in one widget and quarter-on-quarter in the next.
    """
    tree = parse(f"PCT_CHANGE([m:a], {word})", retention_days=None)
    assert isinstance(tree, PercentChange)
    assert tree.grain == word.lower()
    assert declared_grain(tree) == word.lower()


def test_the_grain_word_is_case_insensitive_like_every_other_word() -> None:
    assert _shape(parse("LAG([m:a], 1, month)")) == _shape(parse("LAG([m:a], 1, MONTH)"))


def test_the_grain_is_part_of_the_tree_and_not_an_annotation() -> None:
    """Two grains are two different formulas, so they cannot compare equal — a
    measure whose grain lived outside the tree would hash and diff as one thing."""
    assert _shape(parse("PCT_CHANGE([m:a], MONTH)")) != _shape(parse("PCT_CHANGE([m:a], QUARTER)"))


def test_a_formula_with_no_period_function_declares_no_grain() -> None:
    assert declared_grain(parse("SAFE_DIV([m:a], [m:b])")) is None


def test_an_unknown_period_word_names_the_ones_that_exist() -> None:
    error = _refuse("PCT_CHANGE([m:a], WEEK)")
    assert isinstance(error, UnknownPeriodGrain)
    assert "WEEK" in error.message
    for word in GRAIN_WORDS:
        assert word in error.message


def test_an_absurdly_long_period_word_is_not_repeated_back() -> None:
    error = _refuse(f"PCT_CHANGE([m:a], {'W' * 200})")
    assert isinstance(error, UnknownPeriodGrain)
    assert "W" * 200 not in error.message


def test_a_period_function_written_without_its_grain_is_taught_the_whole_rule() -> None:
    """The old one- and two-input forms. The refusal has to say that the period is
    part of what the measure MEANS, not report a count and leave them guessing."""
    for source, example in (
        ("PCT_CHANGE([m:a])", "PCT_CHANGE([m:a], MONTH)"),
        ("LAG([m:a], 2)", "LAG([m:a], 1, MONTH)"),
    ):
        error = _refuse(source)
        assert isinstance(error, WrongArgumentType)
        assert example in error.message


def test_the_grain_has_to_be_the_last_input() -> None:
    """``PCT_CHANGE(MONTH, [m:a])`` reads as a different formula and is refused, so
    two people cannot write the same measure two ways."""
    assert isinstance(_refuse("PCT_CHANGE(MONTH, [m:a])"), UnknownFunction)
    assert isinstance(_refuse("LAG(MONTH, [m:a], 1)"), UnknownFunction)


@pytest.mark.parametrize(
    "source",
    [
        "PCT_CHANGE([m:a], MONTH) + PCT_CHANGE([m:b], QUARTER)",
        "LAG([m:a], 1, MONTH) - LAG([m:a], 1, YEAR)",
        "PCT_CHANGE(LAG([m:a], 1, QUARTER), MONTH)",
    ],
)
def test_one_formula_may_compare_over_one_period_only(source: str) -> None:
    error = _refuse(source)
    assert isinstance(error, MixedPeriodGrains)


def test_mixed_grains_are_refused_even_with_no_retention_bound_applied() -> None:
    """The grain rule is about MEANING, so it does not depend on the window."""
    with pytest.raises(MixedPeriodGrains):
        expr_module.parse("PCT_CHANGE([m:a], MONTH) + PCT_CHANGE([m:b], YEAR)", retention_days=None)


# --- the periods a formula reads ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("[m:a] + 1", (0,)),
        ("PCT_CHANGE([m:a], MONTH)", (0, 1)),
        ("LAG([m:a], 3, MONTH)", (0, 3)),
        ("[m:a] - LAG([m:a], 1, MONTH)", (0, 1)),
        # The trap: a lag OUTSIDE a change reads two and three periods back, not
        # one and two. Getting this wrong is silently a whole period off.
        ("LAG(PCT_CHANGE([m:a], MONTH), 2, MONTH)", (0, 2, 3)),
        ("LAG(LAG([m:a], 2, MONTH), 3, MONTH)", (0, 5)),
    ],
)
def test_period_offsets_compose_rather_than_accumulate(
    source: str, expected: tuple[int, ...]
) -> None:
    assert period_offsets(parse(source, retention_days=None)) == expected


def test_the_query_s_own_period_is_always_read() -> None:
    """Even a formula that is nothing but a lag: other figures in the same result
    are at the query's own window, and the scan has to admit it."""
    assert period_offsets(parse("LAG([m:a], 4, MONTH)", retention_days=None))[0] == 0


# --- the retention bound (D-196) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("retention_days", "grain", "expected"),
    [
        # The shipped default. 95 days is three whole months, one whole quarter,
        # and not a year — so a year-on-year comparison cannot be made from it.
        (95, "month", 3),
        (95, "quarter", 1),
        (95, "year", 0),
        (400, "month", 12),
        (400, "quarter", 4),
        (400, "year", 1),
        (0, "month", 0),
    ],
)
def test_max_lag_periods_converts_days_to_periods_at_the_longest_period(
    retention_days: int, grain: str, expected: int
) -> None:
    assert max_lag_periods(retention_days, cast("PeriodGrain", grain)) == expected


def test_the_conversion_uses_the_longest_a_period_can_be() -> None:
    """The refusing direction. Dividing by an average month would accept a reach
    that is answerable in February and empty in March — an empty answer that reads
    as a real figure of nothing, which is the whole of D-196."""
    assert GRAIN_MAX_DAYS["month"] == 31
    assert max_lag_periods(GRAIN_MAX_DAYS["month"] * 3, "month") == 3
    assert max_lag_periods(GRAIN_MAX_DAYS["month"] * 3 - 1, "month") == 2


def test_the_structural_ceiling_still_caps_a_very_long_retention_window() -> None:
    assert max_lag_periods(1_000_000, "month") == MAX_LAG_PERIODS


def test_a_negative_retention_window_is_a_programming_error_not_a_refusal() -> None:
    with pytest.raises(ValueError, match="retention_days"):
        max_lag_periods(-1, "month")


def test_the_reach_is_bounded_by_retention_and_the_refusal_names_the_limit() -> None:
    allowed = max_lag_periods(RETENTION_DAYS, "month")
    assert parse(f"LAG([m:a], {allowed}, MONTH)") is not None
    error = _refuse(f"LAG([m:a], {allowed + 1}, MONTH)")
    assert isinstance(error, LagBeyondRetention)
    assert str(RETENTION_DAYS) in error.message
    assert str(allowed) in error.message


def test_a_grain_no_window_can_cover_says_so_rather_than_naming_a_limit_of_zero() -> None:
    error = _refuse_with("PCT_CHANGE([m:a], YEAR)", retention_days=95)
    assert isinstance(error, LagBeyondRetention)
    assert "less than one year" in error.message
    assert "95 days" in error.message


def test_the_bound_is_measured_on_the_composed_reach_not_the_written_number() -> None:
    """``LAG(PCT_CHANGE(x), n)`` reaches ``n + 1`` periods back, so the bound has to
    be checked on what the formula actually reads."""
    allowed = max_lag_periods(RETENTION_DAYS, "month")
    assert isinstance(
        _refuse(f"LAG(PCT_CHANGE([m:a], MONTH), {allowed}, MONTH)"), LagBeyondRetention
    )


def test_parse_will_not_guess_a_retention_window() -> None:
    """No default, in either direction (the ICAAP ``record=`` lesson): the write
    path passes the deployment's window and an authorization walk passes ``None``,
    and a caller that says neither is a caller that has not decided."""
    with pytest.raises(TypeError):
        expr_module.parse("[m:a]")  # type: ignore[call-arg]


def test_no_retention_window_means_the_bound_is_not_applied_at_all() -> None:
    """Which figures a stored formula names must not change when a deployment
    shortens its retention: that is an AUTHORIZATION question, and a measure that
    vanished from its own author's list on a configuration change would be wrong."""
    tree = expr_module.parse("LAG([m:a], 40, YEAR)", retention_days=None)
    assert referenced_members(tree) == ("a",)


# --- referenced_members -------------------------------------------------------------------------


def test_referenced_members_reports_every_position_in_order() -> None:
    tree = parse(KITCHEN_SINK)
    assert referenced_members(tree) == ("a", "b", "c", "d", "e", "f", "g")


def test_every_node_kind_is_reachable_from_source() -> None:
    """If a node kind cannot be written, nothing above can be trusted about it."""
    reached = {type(node) for node in walk(parse(KITCHEN_SINK))}
    assert set(NODE_KINDS) <= reached


def test_referenced_members_de_duplicates_but_keeps_first_use_order() -> None:
    tree = parse("SAFE_DIV([m:b], [m:a]) + [m:b] - [m:a]")
    assert referenced_members(tree) == ("b", "a")


def test_referenced_members_reaches_a_figure_buried_in_every_slot() -> None:
    """One figure per operand position of every node kind, checked as a set: a
    walk that skipped one slot would show up here as a missing id."""
    tree = parse(
        "IF(LAG([m:s1], 1, MONTH) = PCT_CHANGE([m:s2], MONTH) "
        "OR NOT -[m:s3] < SAFE_DIV([m:s4], [m:s5]), [m:s6] / [m:s7], [m:s8] * [m:s9])"
    )
    assert set(referenced_members(tree)) == {f"s{index}" for index in range(1, 10)}


def test_the_traversal_has_exactly_one_definition() -> None:
    """Every node kind inherits ``children`` from ``Expr``, which reads the
    dataclass FIELDS. A node that overrode it could list its operands wrongly
    and take a figure out of the authorization walk."""
    overriding = [kind.__name__ for kind in NODE_KINDS if "children" in kind.__dict__]
    assert overriding == []


def test_children_are_the_expression_fields_in_written_order() -> None:
    tree = parse("IF([m:a] > [m:b], [m:c], [m:d])")
    assert isinstance(tree, Conditional)
    assert tree.children == (tree.condition, tree.when_true, tree.when_false)


def test_referenced_members_and_depth_do_not_recurse() -> None:
    """A tree deeper than the parser would ever build must still be walkable:
    the guards are bounds on INPUT, not an excuse for a fragile walk."""
    node: Expr = MemberReference(member_id="deep", position=0)
    for _ in range(5_000):
        node = Arithmetic(
            operator="+", left=node, right=NumberLiteral(value=Decimal(1), position=0), position=0
        )
    assert referenced_members(node) == ("deep",)
    assert depth(node) == 5_001


# --- the security property, as a property --------------------------------------------------------

_FIGURE_IDS = st.sampled_from(
    ["a", "b", "m0", "m1", "m2", "loans.balance_rc", "engine.lcr.basel.official", "x_y.z_1"]
)
_LEAVES = st.one_of(
    st.integers(min_value=1, max_value=9_999).map(str),
    st.sampled_from(["1.5", "0.25", "100"]),
    _FIGURE_IDS.map(lambda member_id: f"[m:{member_id}]"),
)


def _conditions(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    comparison = st.builds(
        lambda left, operator, right: f"({left} {operator} {right})",
        inner,
        st.sampled_from(["=", "!=", "<", "<=", ">", ">="]),
        inner,
    )
    return st.one_of(
        comparison,
        st.builds(
            lambda left, operator, right: f"({left} {operator} {right})",
            comparison,
            st.sampled_from(["AND", "OR"]),
            comparison,
        ),
        comparison.map(lambda condition: f"NOT {condition}"),
    )


def _wider(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    return st.one_of(
        st.builds(
            lambda left, operator, right: f"({left} {operator} {right})",
            inner,
            st.sampled_from(["+", "-", "*", "/"]),
            inner,
        ),
        st.builds(lambda left, right: f"SAFE_DIV({left}, {right})", inner, inner),
        st.builds(lambda value: f"PCT_CHANGE({value}, MONTH)", inner),
        st.builds(
            lambda value, periods: f"LAG({value}, {periods}, MONTH)",
            inner,
            st.integers(min_value=1, max_value=MAX_LAG_PERIODS),
        ),
        st.builds(lambda value: f"(-{value})", inner),
        st.builds(
            lambda condition, yes, no: f"IF({condition}, {yes}, {no})",
            _conditions(inner),
            inner,
            inner,
        ),
    )


VALID_FORMULAS = st.recursive(_LEAVES, _wider, max_leaves=10)

#: Text made of the language's own characters, the ones an injection uses, and
#: a few script lookalikes. Most of it will not parse; that is the point.
HOSTILE_TEXT = st.text(
    alphabet="abcmxy_.:0123456789 \t[]()+-*/<>=!,'\"`;#\\\x00\u00a0\u0430\uff0bANDORIFLGSVEPCH",
    max_size=80,
)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(source=VALID_FORMULAS)
def test_referenced_members_reports_every_figure_the_source_names(source: str) -> None:
    """THE security property. Every ``[m:id]`` written in the source must come
    back from ``referenced_members``, because that tuple is what the grant
    check is run over. Equality is asserted as well — nothing is invented — but
    the direction that matters is the superset: an id that is in the text and
    not in the tuple is a figure computed without a binding."""
    try:
        tree = parse(source, retention_days=None)
    except (ExpressionTooDeep, ExpressionTooLong, TooManyMemberReferences):
        reject()

    written = set(FIGURE_IN_SOURCE.findall(source))
    reported = set(referenced_members(tree))
    assert written <= reported, f"figures lost by the walk: {sorted(written - reported)}"
    assert reported == written
    assert len(referenced_members(tree)) <= MAX_MEMBER_REFERENCES
    assert depth(tree) <= MAX_DEPTH
    assert tree.result_type == "number"


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(source=HOSTILE_TEXT)
def test_hostile_text_either_refuses_by_name_or_parses_completely(source: str) -> None:
    """No third outcome. Not an uncaught exception, not a ``RecursionError``,
    not a tree whose figures the walk cannot see."""
    try:
        tree = parse(source)
    except ExpressionError as error:
        _assert_posture(error, source)
        return
    assert set(FIGURE_IN_SOURCE.findall(source)) == set(referenced_members(tree))
    assert tree.result_type == "number"
    assert depth(tree) <= MAX_DEPTH


#: Tails that KEEP a formula valid, mixed in with the hostile ones so the
#: appended-figure case is actually reached rather than always refused.
CONTINUATIONS = st.sampled_from(
    ["", " ", " + [m:z]", " * 2", " - SAFE_DIV([m:z], [m:zz])", " / [m:zz]"]
)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(prefix=VALID_FORMULAS, suffix=st.one_of(CONTINUATIONS, HOSTILE_TEXT))
def test_a_valid_formula_with_something_appended_never_smuggles_a_figure(
    prefix: str, suffix: str
) -> None:
    """The classic shape: a legitimate formula the reviewer reads, and a tail
    that does the work. Either the tail is refused by name, or it is part of the
    formula — and then every figure in it is reported like any other."""
    source = f"{prefix}{suffix}"
    assume(len(source) <= MAX_EXPRESSION_LENGTH)
    try:
        tree = parse(source)
    except ExpressionError as error:
        _assert_posture(error, source)
        return
    assert set(FIGURE_IN_SOURCE.findall(source)) == set(referenced_members(tree))


def test_a_figure_appended_to_a_formula_is_reported_like_any_other() -> None:
    """The deterministic form of the property above, so it is visible even if a
    generator ever stopped reaching it."""
    tree = parse("SAFE_DIV([m:a], [m:b]) + [m:sneaked_in]")
    assert referenced_members(tree) == ("a", "b", "sneaked_in")


# --- injection ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "[m:x]; DROP TABLE bi_fact_position_daily",
        "[m:x]; SELECT 1",
        "[m:x] UNION SELECT 1",
        "[m:x] UNION ALL SELECT balance_rc FROM bi_fact_position_daily",
        "[m:x'] + 1",
        '[m:x"] + 1',
        "[m:`x`]",
        "[m:x) OR 1=1]",
        "[m:x--] + 1",
        "[m:x/*comment*/]",
        "[m:x\x00]",
        "[m:x]\x00",
        "[m:\u0430]",
        "[m:\uff58]",
        "[m:x]\u00a0+ 1",
        "[m:x] \\ [m:y]",
        "[m:x] # [m:y]",
        "[m:x] /* [m:y] */",
        "1 /*! + 1 */",
        "\u0661\u0662\u0663",
        "[m:x] \uff0b 1",
        "SAFE_DIV([m:x], [m:y]) -- and then anything",
    ],
)
def test_injection_attempts_are_ordinary_refusals(source: str) -> None:
    """None of these may be special. Each is either a named refusal or, where
    the characters happen to be language characters, an ordinary formula — and
    in neither case does the text reach anything but this parser."""
    try:
        tree = parse(source)
    except ExpressionError as error:
        _assert_posture(error, source)
        return
    assert set(FIGURE_IN_SOURCE.findall(source)) == set(referenced_members(tree))


def test_a_semicolon_is_not_a_statement_separator_it_is_not_a_character() -> None:
    error = _refuse("[m:x]; DROP TABLE t")
    assert isinstance(error, UnexpectedCharacter)
    assert error.position == len("[m:x]")


def test_a_trailing_word_is_trailing_content_and_is_not_quoted_back() -> None:
    source = "[m:x] UNION SELECT 1"
    error = _refuse(source)
    assert isinstance(error, TrailingContent)
    assert "UNION" not in error.message
    assert "SELECT" not in error.message


def test_a_quote_inside_a_reference_makes_it_malformed_not_an_identifier() -> None:
    """A figure id is matched by SHAPE. Reading to the closing bracket instead
    would make ``[m:x' OR 1=1]`` a perfectly well-formed reference carrying an
    arbitrary string onward to whatever looks the id up."""
    for source in ("[m:x'] ", '[m:x"]', "[m:`x`]", "[m:x;y]", "[m:X]", "[m:x ]", "[m: x]"):
        assert isinstance(_refuse(source), MalformedMemberReference)


def test_script_lookalikes_are_not_letters() -> None:
    """``\\w`` and ``\\d`` match every script in Python; the lexer uses explicit
    ASCII classes so a Cyrillic ``a`` cannot pose as a figure id and a
    Arabic-Indic digit cannot pose as a number."""
    assert isinstance(_refuse("[m:\u0430]"), MalformedMemberReference)
    assert isinstance(_refuse("\u0661 + 1"), UnexpectedCharacter)
    assert isinstance(_refuse("[m:a] \uff0b 1"), UnexpectedCharacter)
    assert isinstance(_refuse("[m:a]\u00a0+ 1"), UnexpectedCharacter)


def test_a_comment_marker_is_two_minus_signs_and_nothing_more() -> None:
    """``--`` has no meaning here, so ``[m:a] -- 1`` is ``a - (-1)``. What it may
    never be is text the parser skips."""
    tree = parse("[m:a] -- 1")
    assert isinstance(tree, Arithmetic)
    assert tree.operator == "-"
    assert isinstance(tree.right, Negation)


def test_a_null_byte_is_refused_wherever_it_appears() -> None:
    for source in ("\x00", "[m:a]\x00", "[m:a] + \x001"):
        assert isinstance(_refuse(source), UnexpectedCharacter)


def test_a_hand_built_reference_with_a_hostile_id_is_refused_at_construction() -> None:
    """The bound holds for callers that never go through ``parse`` — a stored
    formula rebuilt from a saved tree, say."""
    for member_id in ("x'; DROP TABLE t", "X", "", "a b", "a..b", "\u0430"):
        with pytest.raises(MalformedMemberReference):
            MemberReference(member_id=member_id, position=0)


def test_a_hand_built_node_cannot_be_ill_typed() -> None:
    comparison = parse("IF([m:a] > 1, 1, 0)")
    assert isinstance(comparison, Conditional)
    with pytest.raises(WrongArgumentType):
        Arithmetic(
            operator="+",
            left=comparison.condition,
            right=NumberLiteral(value=Decimal(1), position=0),
            position=0,
        )


# --- bounds ---------------------------------------------------------------------------------------


def test_a_formula_longer_than_the_limit_is_refused_by_name() -> None:
    error = _refuse("1 + " * MAX_EXPRESSION_LENGTH)
    assert isinstance(error, ExpressionTooLong)
    assert str(MAX_EXPRESSION_LENGTH) in error.message


def test_the_length_limit_is_checked_before_anything_else_reads_the_text() -> None:
    """A pathological formula must cost one length comparison, not a lex."""
    error = _refuse("\x00" * (MAX_EXPRESSION_LENGTH + 1))
    assert isinstance(error, ExpressionTooLong)


def test_nesting_by_brackets_is_refused_by_name_and_never_exhausts_the_stack() -> None:
    opened = MAX_DEPTH * 4
    error = _refuse("(" * opened + "1" + ")" * opened)
    assert isinstance(error, ExpressionTooDeep)
    assert str(MAX_DEPTH) in error.message


def test_nesting_by_functions_is_refused_by_name() -> None:
    depth_attempted = MAX_DEPTH * 2
    source = "PCT_CHANGE(" * depth_attempted + "[m:a]" + ")" * depth_attempted
    assert isinstance(_refuse(source), ExpressionTooDeep)


def test_a_long_flat_chain_is_refused_too() -> None:
    """The parser builds a left-associative chain in a LOOP, so its own descent
    counter never fires — and the tree it would hand the compiler is one level
    deep per term. The finished tree is measured separately for exactly this."""
    error = _refuse("1" + "+1" * (MAX_DEPTH + 5))
    assert isinstance(error, ExpressionTooDeep)


def test_a_chain_just_inside_the_depth_limit_still_parses() -> None:
    """A limit that refused legitimate formulas would be its own defect."""
    tree = parse("1" + "+1" * (MAX_DEPTH - 2))
    assert depth(tree) == MAX_DEPTH - 1


def test_too_many_distinct_figures_is_refused_by_name() -> None:
    source = "+".join(f"[m:m{index}]" for index in range(MAX_MEMBER_REFERENCES + 1))
    error = _refuse(source)
    assert isinstance(error, TooManyMemberReferences)
    assert str(MAX_MEMBER_REFERENCES) in error.message


def test_the_figure_limit_counts_distinct_ids_not_uses() -> None:
    """Repeating a figure costs the compiler nothing, so charging for it would
    refuse ``SAFE_DIV([m:a] - [m:b], [m:a])`` for no reason."""
    source = " + ".join(["[m:a]", "[m:b]"] * ((MAX_DEPTH - 2) // 2))
    tree = parse(source)
    assert referenced_members(tree) == ("a", "b")


def test_the_figure_limit_matches_the_measures_one_query_can_carry() -> None:
    """The compiler has to resolve every figure of a calculated measure into one
    query; a formula naming more than a query can hold could never compile."""
    assert MAX_MEMBER_REFERENCES == BI_MAX_MEASURES


# --- what a refusal gives back -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", EmptyExpression),
        ("1 +", UnexpectedEndOfExpression),
        ("(1", UnbalancedParenthesis),
        ("SAFE_DIV(1, 2", UnbalancedParenthesis),
        ("1 1", TrailingContent),
        ("1)", TrailingContent),
        ("*", UnexpectedToken),
        ("==", UnexpectedToken),
        (",", UnexpectedToken),
        ("$", UnexpectedCharacter),
        ("[m:]", MalformedMemberReference),
        ("[x:a]", MalformedMemberReference),
        ("NOPE(1)", UnknownFunction),
        ("SAFE_DIV([m:a])", WrongArgumentCount),
        ("NOT 1", WrongArgumentType),
        ("[m:a] > 1", NonNumericExpression),
        ("1/0", DivisionByZero),
        ("PCT_CHANGE([m:a], FORTNIGHT)", UnknownPeriodGrain),
        ("PCT_CHANGE([m:a], MONTH) + PCT_CHANGE([m:b], YEAR)", MixedPeriodGrains),
        # 400 days of retention is one whole year, so two years back is past it.
        ("LAG([m:a], 2, YEAR)", LagBeyondRetention),
    ],
)
def test_each_failure_shape_has_its_own_named_error(
    source: str, expected: type[ExpressionError]
) -> None:
    assert isinstance(_refuse(source), expected)


def test_error_codes_are_unique_across_the_named_failures() -> None:
    """Codes are what telemetry and the error envelope key on; two failures
    sharing one would make them indistinguishable in production."""
    named = [
        kind
        for kind in vars(expr_module).values()
        if isinstance(kind, type)
        and issubclass(kind, ExpressionError)
        and kind is not ExpressionError
    ]
    codes = [kind.code for kind in named]
    assert len(codes) == len(set(codes)), sorted(codes)
    assert len(named) >= 14


def test_an_excerpt_is_bounded_and_made_of_language_characters_only() -> None:
    source = "[m:a] + `rm -rf /`;\x00" + "\u0430" * 200
    error = _refuse(source)
    assert len(error.excerpt) <= MAX_EXCERPT_LENGTH
    assert set(error.excerpt) <= ALPHABET | {REDACTION}
    assert REDACTION in error.excerpt, "the hostile characters were kept, not replaced"
    assert "`" not in error.excerpt
    assert "\x00" not in error.excerpt
    assert "\u0430" not in error.excerpt


def test_an_excerpt_still_shows_the_caller_where_they_went_wrong() -> None:
    """Sanitising is not the same as withholding: the window has to be useful."""
    error = _refuse("[m:a] + [m:b] ; 1")
    assert "1" in error.excerpt or "?" in error.excerpt
    assert error.position == len("[m:a] + [m:b] ")


# --- what this module deliberately does NOT decide -----------------------------------------------


def test_a_figure_id_the_catalogue_does_not_know_is_a_well_formed_reference() -> None:
    """Membership is the COMPILER's question, not the parser's.

    ``compile`` resolves ids against the catalogue and raises ``UnknownMember``
    (``app/services/bi/errors.py``) for one it does not hold, which is the same
    refusal a query gets for the same id. If the parser answered it instead, a
    parse failure would become a statement about which figures exist — a probe
    of the catalogue through the formula box — and this pure module would have
    to depend on the catalogue to say so. So a well-shaped id parses, whatever
    it names, and ``referenced_members`` hands it on to be resolved.
    """
    tree = parse("[m:no.such.figure.anywhere] + [m:loans.balance_rc]")
    assert referenced_members(tree) == ("no.such.figure.anywhere", "loans.balance_rc")


def test_the_module_reaches_nothing_but_the_standard_library() -> None:
    """It is scanned by the BI architecture guards, but the contract is tighter
    than those: no application import at all, so the parser can be reasoned
    about — and fuzzed — with nothing else loaded."""
    source = Path(str(expr_module.__file__)).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in python_ast.walk(python_ast.parse(source)):
        if isinstance(node, python_ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, python_ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert [
        name for name in imported if name.split(".")[0] in {"app", "sqlalchemy", "pydantic"}
    ] == []


def test_the_parser_produces_no_sql_and_holds_no_text_that_looks_like_it() -> None:
    """The one thing a formula language must never grow. Pinned here as well as
    in ``tests/architecture/test_bi_compiler_injection.py`` because that guard
    scans the tree, and this is the module where the temptation lives."""
    source = Path(str(expr_module.__file__)).read_text(encoding="utf-8")
    for keyword in ("SELECT", "INSERT", "UPDATE", "DELETE", "FROM ", "WHERE", "JOIN"):
        assert keyword not in source, keyword


# ---------------------------------------------------------------------------
# A9-01: the formula language must not be a denial-of-service surface
# ---------------------------------------------------------------------------


def _nested_percent_change(depth: int) -> str:
    text = "[m:loans.balance_rc]"
    for _ in range(depth):
        text = f"PCT_CHANGE({text}, MONTH)"
    return text


def test_a_deeply_nested_change_is_answered_in_bounded_time() -> None:
    """Audit A9-01, a BLOCKER, demonstrated and fixed.

    ``PCT_CHANGE`` reads its operand at two periods, so an unmemoised walk over a
    chain of N nested ones explores 2^N paths. Measured before the fix: 0.33 s at
    depth 20, 1.34 s at 22, 5.35 s at 24 — doubling per level — on a formula of
    under 500 characters against a 2,000-character limit and a depth limit of 64.
    A single authenticated reader could submit a formula the parser ACCEPTS and
    burn a core for longer than the universe has existed, on a synchronous
    handler, with the read budget unable to meter it because the validation route
    writes no query-log row.

    The bound here is deliberately generous. A tight one would be flaky on a busy
    machine; anything in seconds proves the exponential is gone, because the
    pre-fix cost at this depth was astronomically larger than any timeout.
    """
    source = _nested_percent_change(MAX_DEPTH - 1)
    assert len(source) < MAX_EXPRESSION_LENGTH, "the hostile formula must be one the parser accepts"

    started = time.perf_counter()
    node = parse(source, retention_days=None)
    offsets = period_offsets(node)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"period_offsets took {elapsed:.1f}s; the memo is gone"
    # And the answer is still right: each nesting level adds one period back.
    assert offsets == tuple(range(MAX_DEPTH)), offsets


def test_memoising_did_not_change_which_periods_are_read() -> None:
    """The memo is on the PAIR, so it cannot change the set of offsets.

    Asserted against the unmemoised algorithm itself rather than against
    hand-written expectations, over every shape that composes a period: a bare
    figure, one change, a lag of a change, a lag beside a change, a condition, and
    a nest shallow enough for the old algorithm to finish.
    """

    def unmemoised(node: Expr) -> tuple[int, ...]:
        found = {0}
        pending: list[tuple[Expr, int]] = [(node, 0)]
        while pending:
            current, offset = pending.pop()
            if isinstance(current, Lag):
                pending.append((current.operand, offset + current.periods))
                continue
            if isinstance(current, PercentChange):
                pending.append((current.operand, offset))
                pending.append((current.operand, offset + 1))
                continue
            if isinstance(current, MemberReference):
                found.add(offset)
                continue
            pending.extend((child, offset) for child in current.children)
        return tuple(sorted(found))

    sources = (
        "[m:loans.balance_rc]",
        "PCT_CHANGE([m:loans.balance_rc], MONTH)",
        "LAG(PCT_CHANGE([m:loans.balance_rc], MONTH), 2, MONTH)",
        "SAFE_DIV(PCT_CHANGE([m:loans.balance_rc], MONTH), LAG([m:deposits.balance_rc], 3, MONTH))",
        "IF(PCT_CHANGE([m:loans.balance_rc], QUARTER) > 0, "
        "LAG([m:loans.balance_rc], 1, QUARTER), 0)",
        _nested_percent_change(8),
    )
    for source in sources:
        node = parse(source, retention_days=None)
        assert period_offsets(node) == unmemoised(node), source
