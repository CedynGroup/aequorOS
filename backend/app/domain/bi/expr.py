"""The calculated-measure formula language: source text in, a typed AST out.

A calculated measure (spec §Phase 3) is a formula a user writes over figures
that already exist in the catalogue — ``SAFE_DIV([m:loans.non_performing_rc],
[m:loans.balance_rc])``, ``PCT_CHANGE([m:deposits.balance_rc])``. This module
is the ONE place that text becomes structure.

It produces an AST. It NEVER produces SQL, and it cannot: nothing here imports
a database driver, a session or a service, and the only strings it builds are
error messages. Turning the AST into a SQLAlchemy expression is the compiler's
job (``app/services/bi/compiler.py``), and the compiler is also the one that
decides whether a referenced id is a catalogue member at all — see
:class:`MemberReference`.

Four properties the rest of the BI plane rests on:

1. **:func:`referenced_members` is complete.** Authorization walks a calculated
   measure by walking the ids it names; an id that the walk misses is a figure
   read without a binding. The walk is therefore STRUCTURAL — it reads the
   dataclass fields of each node rather than a hand-written list of operands —
   so a node type that gains an operand is walked correctly the day it is
   added, and there is no second list to forget.
2. **Bounded by construction.** Length, nesting depth and the number of
   distinct figures are all capped, and each cap refuses by NAME. Nesting is
   capped twice, because the two ways to make a deep tree are different: the
   parser counts its own descent (so ``((((…`` cannot exhaust the interpreter
   stack) and the finished tree is measured iteratively (so ``1+1+1+…``, which
   the parser builds in a loop, cannot hand the compiler a tree it would
   recurse down). Nothing here can hang or raise ``RecursionError``.
3. **A period comparison DECLARES its grain (D-195).** ``PCT_CHANGE`` and
   ``LAG`` each take the period they compare over as their last input, written
   in the formula (``PCT_CHANGE([m:a], MONTH)``), so the text a checker approves
   states the meaning and the same measure cannot render month-on-month in one
   widget and quarter-on-quarter in another under one certification badge. One
   formula may name one grain; how far back it may reach is a property of the
   deployment's retention window, not a constant here (:func:`max_lag_periods`,
   D-196).
4. **Nothing of the caller's text is repeated back unchecked.** Every failure
   is a named :class:`ExpressionError` carrying the offset it happened at.
   ``message`` is fixed copy plus, at most, a value the language itself made
   well-formed (a figure id, a function name); ``excerpt`` is a short window on
   the source in which every character outside the language's own alphabet has
   been replaced. This is the posture of
   ``app/services/bi/errors.py::member_id_for_display``, restated for text that
   arrives as a formula rather than as an id.

Positions are 0-based offsets into the source. Messages count characters from
1, the way an editor does.
"""

from __future__ import annotations

import abc
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _typeshed import DataclassInstance

__all__ = [
    "ALPHABET",
    "FUNCTION_NAMES",
    "GRAIN_LABELS",
    "GRAIN_MAX_DAYS",
    "GRAIN_WORDS",
    "MAX_DEPTH",
    "MAX_EXCERPT_LENGTH",
    "MAX_EXPRESSION_LENGTH",
    "MAX_LAG_PERIODS",
    "MAX_MEMBER_REFERENCES",
    "REDACTION",
    "Arithmetic",
    "ArithmeticOperator",
    "Comparison",
    "ComparisonOperator",
    "Conditional",
    "DivisionByZero",
    "EmptyExpression",
    "Expr",
    "ExprType",
    "ExpressionError",
    "ExpressionTooDeep",
    "ExpressionTooLong",
    "Lag",
    "LagBeyondRetention",
    "Logical",
    "LogicalNot",
    "LogicalOperator",
    "MalformedMemberReference",
    "MemberReference",
    "MixedPeriodGrains",
    "Negation",
    "NonNumericExpression",
    "NumberLiteral",
    "PercentChange",
    "PeriodGrain",
    "SafeDivide",
    "TooManyMemberReferences",
    "TrailingContent",
    "UnbalancedParenthesis",
    "UnexpectedCharacter",
    "UnexpectedEndOfExpression",
    "UnexpectedToken",
    "UnknownFunction",
    "UnknownPeriodGrain",
    "WrongArgumentCount",
    "WrongArgumentType",
    "declared_grain",
    "depth",
    "max_lag_periods",
    "parse",
    "period_offsets",
    "referenced_members",
    "walk",
]


# --- limits -----------------------------------------------------------------------------------

#: Longest formula accepted, in characters. A calculated measure is written and
#: re-read by a person in a single editor field; two thousand characters is far
#: past anything legible and small enough that lexing is trivially bounded.
MAX_EXPRESSION_LENGTH: Final = 2_000

#: Deepest tree accepted, counting the root as level 1. It has to clear the
#: reference cap comfortably, because a plain sum of the maximum number of
#: figures is already that many levels deep on its left spine; sixty-four leaves
#: room for real nesting while keeping every recursive walk of the tree (this
#: parser, the compiler, a renderer) inside a few hundred interpreter frames.
MAX_DEPTH: Final = 64

#: Most DISTINCT figures one formula may name. The compiler has to resolve all
#: of them into a single query, and a query carries at most this many measures
#: (``app/schemas/bi.py::BI_MAX_MEASURES``); a formula naming more could never
#: be compiled, so it is cheapest to refuse it here. The two numbers are pinned
#: equal by ``tests/domain/bi/test_expr.py``.
MAX_MEMBER_REFERENCES: Final = 25

#: The language's own ceiling on ``LAG``'s reach, in periods. It is a STRUCTURAL
#: bound — it keeps the tree, and therefore the compiled statement, finite — and
#: it is deliberately NOT the policy bound. What a formula may actually ask for
#: is :func:`max_lag_periods`, which is derived from how long the marts keep
#: daily history (D-196); this number only stops an unbounded tree.
MAX_LAG_PERIODS: Final = 60

#: The period a calculated measure compares over, DECLARED in the formula
#: (D-195). ``PCT_CHANGE([m:a], MONTH)`` means month-on-month wherever that
#: measure appears, so the same label and the same certification badge cannot
#: render as one comparison in a widget and a different one in the next.
#:
#: Exactly three, and not by preference: these are the grains ``bi_dim_date``
#: carries an ``is_last_in_*`` flag for, which is what fixes where "the period
#: before" ENDS for a stock measure (D-014). A grain with no flag would have to
#: invent that, and inventing it is how a figure comes out a whole period off.
PeriodGrain = Literal["month", "quarter", "year"]

#: The word a formula writes for each grain, in the order a message lists them.
#: Matched case-insensitively, like the function names and the connectives.
GRAIN_WORDS: Final[Mapping[str, PeriodGrain]] = MappingProxyType(
    {"MONTH": "month", "QUARTER": "quarter", "YEAR": "year"}
)

#: How a grain is named in production copy. A separate map from
#: :data:`GRAIN_WORDS` because a message reads "the month before", lower case and
#: in prose, while the formula is written in the language's own upper-case word.
GRAIN_LABELS: Final[Mapping[PeriodGrain, str]] = MappingProxyType(
    {"month": "month", "quarter": "quarter", "year": "year"}
)

#: The LONGEST a period of each grain can be, in days. The maximum rather than an
#: average, because the bound derived from it decides whether a formula is
#: accepted at all: taking 30 for a month would accept a formula that is
#: answerable in February and comes back empty in March, which is precisely the
#: "an empty answer looks like a real figure of nothing" failure D-196 refuses.
GRAIN_MAX_DAYS: Final[Mapping[PeriodGrain, int]] = MappingProxyType(
    {"month": 31, "quarter": 92, "year": 366}
)

#: Longest window on the source an error may carry back to the caller.
MAX_EXCERPT_LENGTH: Final = 40

#: Longest function name repeated verbatim in a message.
_NAME_ECHO_LIMIT: Final = 32

#: Most inputs any function takes; a longer call is refused before its
#: arguments are built rather than after.
_MAX_ARGUMENTS: Final = 3

#: The two functions whose LAST input is a period grain word rather than an
#: expression. Parsed positionally (``_Parser.time_call``) for that reason.
_GRAIN_FUNCTIONS: Final = frozenset({"PCT_CHANGE", "LAG"})


def max_lag_periods(retention_days: int, grain: PeriodGrain) -> int:
    """How far back a formula may look at ``grain``, given the retained window.

    D-196: the reach of a period comparison is the daily retention window
    (``BI_DAILY_RETENTION_DAYS``), because past it the marts no longer hold the
    rows and the answer comes back EMPTY — which on a chart is indistinguishable
    from a real figure of nothing. The bound is therefore a property of the
    deployment, checked when the formula is written, and not a number in this
    module.

    The conversion is days to PERIODS at the grain's longest length
    (:data:`GRAIN_MAX_DAYS`), which is the refusing direction: a formula accepted
    here is answerable in every month of the year, not only in the short ones.
    :data:`MAX_LAG_PERIODS` still caps the result, so the tree stays finite
    however long a deployment keeps its history.

    Zero is a legitimate answer and means what it says: a window shorter than one
    period of this grain supports no comparison at that grain at all.
    """
    if retention_days < 0:
        raise ValueError("retention_days cannot be negative")
    return min(MAX_LAG_PERIODS, retention_days // GRAIN_MAX_DAYS[grain])


#: Every character the language itself can contain. An error excerpt is mapped
#: onto this set, so what comes back to a caller is always a fragment of a
#: formula and never arbitrary bytes.
ALPHABET: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:,()[]+-*/=<>! "
)

#: What an excerpt shows in place of anything outside :data:`ALPHABET`. It is
#: deliberately NOT a language character, so a caller reading an excerpt can
#: tell a question mark they typed (they cannot — it is not in the alphabet)
#: from one this module put there.
REDACTION: Final = "?"


# --- errors -----------------------------------------------------------------------------------


class ExpressionError(Exception):
    """A formula this module refuses, with the offset it was refused at.

    Never raised bare: every refusal is one of the named subclasses below, each
    with a stable ``code`` for telemetry and an error envelope. The services
    layer wraps these in its own ``BiQueryError`` shape; this module has no
    opinion about HTTP.
    """

    code: str = "invalid_expression"

    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        #: Production copy, safe to show a user as written.
        self.message = message
        #: 0-based offset into the source the failure is about.
        self.position = position
        #: A bounded, alphabet-mapped window on the source; filled by :func:`parse`.
        self.excerpt = ""

    def attach_source(self, source: str) -> None:
        """Fill in :attr:`excerpt` from the source this failure came from."""
        if not self.excerpt:
            self.excerpt = _excerpt(source, self.position)

    def __str__(self) -> str:
        return self.message


class ExpressionTooLong(ExpressionError):
    """The formula is longer than :data:`MAX_EXPRESSION_LENGTH`."""

    code = "expression_too_long"

    def __init__(self, position: int) -> None:
        super().__init__(
            f"A formula can be at most {MAX_EXPRESSION_LENGTH} characters long.", position
        )


class ExpressionTooDeep(ExpressionError):
    """The formula nests deeper than :data:`MAX_DEPTH`."""

    code = "expression_too_deep"

    def __init__(self, position: int) -> None:
        super().__init__(
            f"A formula can be nested at most {MAX_DEPTH} levels deep. "
            "Split it into two calculated measures.",
            position,
        )


class TooManyMemberReferences(ExpressionError):
    """The formula names more distinct figures than :data:`MAX_MEMBER_REFERENCES`."""

    code = "too_many_member_references"

    def __init__(self, position: int) -> None:
        super().__init__(
            f"A formula can use at most {MAX_MEMBER_REFERENCES} different figures.", position
        )


class EmptyExpression(ExpressionError):
    """There is no formula to parse."""

    code = "expression_is_empty"

    def __init__(self) -> None:
        super().__init__("Enter a formula.", 0)


class UnexpectedCharacter(ExpressionError):
    """A character that is not part of the language."""

    code = "unexpected_character"

    def __init__(self, position: int) -> None:
        super().__init__(f"Character {position + 1} is not one a formula can contain.", position)


class MalformedMemberReference(ExpressionError):
    """Something that opens like a figure reference but is not one."""

    code = "malformed_member_reference"

    def __init__(self, position: int) -> None:
        super().__init__(
            f"Character {position + 1} starts something that is not a figure reference. "
            "Write a figure as [m:figure_id], using lower-case letters, digits, "
            "underscores and dots.",
            position,
        )


class UnexpectedToken(ExpressionError):
    """Valid language, in a place it cannot go."""

    code = "unexpected_token"

    def __init__(self, position: int, rule: str) -> None:
        super().__init__(f"Character {position + 1} is not valid here. {rule}", position)


class UnexpectedEndOfExpression(ExpressionError):
    """The formula stops in the middle of something."""

    code = "unexpected_end_of_expression"

    def __init__(self, position: int) -> None:
        super().__init__("The formula ends before it is finished.", position)


class UnbalancedParenthesis(ExpressionError):
    """A bracket that is opened and never closed."""

    code = "unbalanced_parenthesis"

    def __init__(self, position: int) -> None:
        super().__init__(f"A closing bracket is missing at character {position + 1}.", position)


class TrailingContent(ExpressionError):
    """The formula is complete, and then there is more text."""

    code = "trailing_content"

    def __init__(self, position: int) -> None:
        super().__init__(
            f"The formula is complete before character {position + 1}, but more text follows.",
            position,
        )


class UnknownFunction(ExpressionError):
    """A name that is not one of :data:`FUNCTION_NAMES`."""

    code = "unknown_function"

    def __init__(self, position: int, name: str) -> None:
        super().__init__(
            f"There is no function called {name}. A formula can use {_listed(FUNCTION_NAMES)}.",
            position,
        )


class WrongArgumentCount(ExpressionError):
    """A function called with the wrong number of inputs."""

    code = "wrong_argument_count"

    def __init__(self, position: int, name: str, expected: int, given: int) -> None:
        super().__init__(
            f"{name} takes {_counted(expected, 'input')}, but this formula gives it "
            f"{_counted(given, 'input')}.",
            position,
        )


class WrongArgumentType(ExpressionError):
    """A number where a yes/no answer belongs, or the other way round."""

    code = "wrong_argument_type"

    def __init__(self, position: int, rule: str) -> None:
        super().__init__(rule, position)


class NonNumericExpression(ExpressionError):
    """The formula as a whole works out to a yes/no answer, not a figure."""

    code = "expression_is_not_numeric"

    def __init__(self, position: int) -> None:
        super().__init__(
            "A calculated measure has to work out to a number. This formula works out "
            "to a yes or no. Wrap it in IF to turn it into a number.",
            position,
        )


class UnknownPeriodGrain(ExpressionError):
    """A period word that is not one of :data:`GRAIN_WORDS`."""

    code = "unknown_period_grain"

    def __init__(self, position: int, name: str) -> None:
        super().__init__(
            f"There is no period called {name}. A formula can compare by "
            f"{_listed(tuple(GRAIN_WORDS))}.",
            position,
        )


class MixedPeriodGrains(ExpressionError):
    """One formula comparing over two different periods.

    Refused rather than resolved, because a calculated measure declares ONE
    period grain (D-195) and a figure that is month-on-month in one term and
    quarter-on-quarter in another has no single meaning to certify.
    """

    code = "mixed_period_grains"

    def __init__(self, position: int, first: PeriodGrain, second: PeriodGrain) -> None:
        super().__init__(
            "A calculated measure compares one period. This formula asks for both the "
            f"{GRAIN_LABELS[first]} and the {GRAIN_LABELS[second]}. "
            "Write it as two calculated measures.",
            position,
        )


class LagBeyondRetention(ExpressionError):
    """A comparison reaching further back than the marts keep (D-196)."""

    code = "lag_beyond_retention"

    def __init__(
        self, position: int, grain: PeriodGrain, retention_days: int, allowed: int
    ) -> None:
        period = GRAIN_LABELS[grain]
        kept = f"Daily figures are kept for {_counted(retention_days, 'day')}"
        if allowed < 1:
            super().__init__(
                f"{kept}, which is less than one {period}, so no comparison by "
                f"{period} can be made here. Compare by a shorter period.",
                position,
            )
            return
        super().__init__(
            f"{kept}, so a formula can look back at most {_counted(allowed, period)}.",
            position,
        )


class DivisionByZero(ExpressionError):
    """A divisor written as zero."""

    code = "division_by_zero"

    def __init__(self, position: int) -> None:
        super().__init__(
            "Dividing by zero has no answer. Use SAFE_DIV(a, b), which gives no value "
            "when b is zero.",
            position,
        )


# --- the typed tree ---------------------------------------------------------------------------

#: What a node works out to. There are exactly two, and the distinction is the
#: whole of the type system: a calculated measure is a number, and a condition
#: is the only thing that is not.
ExprType = Literal["number", "boolean"]

ArithmeticOperator = Literal["+", "-", "*", "/"]
ComparisonOperator = Literal["=", "!=", "<", "<=", ">", ">="]
LogicalOperator = Literal["and", "or"]

#: The functions the language has, in the order a message lists them.
FUNCTION_NAMES: Final[tuple[str, ...]] = ("SAFE_DIV", "IF", "PCT_CHANGE", "LAG")

#: Inputs per function. ``PCT_CHANGE`` and ``LAG`` count their period grain as
#: one, because the grain is written in the call and is not optional (D-195).
_FUNCTION_ARITY: Final[Mapping[str, int]] = MappingProxyType(
    {"SAFE_DIV": 2, "IF": 3, "PCT_CHANGE": 2, "LAG": 3}
)

_ARITHMETIC_RULE: Final = (
    "Adding, subtracting, multiplying and dividing need a number on each side. "
    "A comparison is a yes or no, not a number."
)
_NEGATION_RULE: Final = "A minus sign has to be followed by a number."
_COMPARISON_RULE: Final = "A comparison needs a number on each side."
_LOGICAL_RULE: Final = "AND and OR need a yes or no on each side, such as a comparison."
_NOT_RULE: Final = "NOT has to be followed by a yes or no, such as a comparison."
_CONDITION_RULE: Final = "The first input to IF has to be a yes or no, such as [m:a] > [m:b]."
_BRANCH_RULE: Final = "Both results of IF have to be numbers."
_SAFE_DIV_RULE: Final = "SAFE_DIV needs a number for both the top and the bottom."
_PCT_CHANGE_RULE: Final = "PCT_CHANGE needs a number."
_LAG_VALUE_RULE: Final = "LAG needs a number as its first input."
_LAG_PERIODS_RULE: Final = (
    "LAG needs a whole number of periods written directly, between 1 and "
    f"{MAX_LAG_PERIODS} — for example LAG([m:a], 1, MONTH)."
)
#: What each period-comparing function needs, in full. A formula written the old
#: way — without the period — lands here rather than on a bare count, because the
#: thing to teach is that the period is part of what the measure MEANS.
_TIME_FUNCTION_RULES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "PCT_CHANGE": (
            "PCT_CHANGE needs a number and the period to compare it with — "
            "for example PCT_CHANGE([m:a], MONTH)."
        ),
        "LAG": (
            "LAG needs a number, how many periods to look back, and the period — "
            "for example LAG([m:a], 1, MONTH)."
        ),
    }
)
_FUNCTION_CALL_RULE: Final = "A function has to be followed by an opening bracket."
_CHAINED_COMPARISON_RULE: Final = (
    "Comparisons cannot be chained. Write the two comparisons joined by AND."
)
_OPERAND_RULE: Final = (
    "A formula can contain numbers, figures written as [m:figure_id], functions and brackets."
)


class Expr(abc.ABC):
    """One node of a parsed formula.

    Every node is a frozen dataclass carrying the offset it starts at. Its
    sub-expressions are read from its FIELDS (:attr:`children`), not from a
    list each node declares, which is what makes :func:`referenced_members`
    impossible to under-report.
    """

    __slots__ = ()

    #: 0-based offset into the source this node starts at.
    position: int

    @property
    @abc.abstractmethod
    def result_type(self) -> ExprType:
        """What this node works out to."""

    @property
    def children(self) -> tuple[Expr, ...]:
        """The sub-expressions this node holds, in the order they are written."""
        return _sub_expressions(self)


@dataclass(frozen=True, slots=True)
class NumberLiteral(Expr):
    """A number written in the formula, kept exact."""

    value: Decimal
    position: int

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class MemberReference(Expr):
    """``[m:id]`` — a figure named by catalogue id.

    Whether the catalogue HAS that id is deliberately not asked here. A parser
    that consulted the catalogue would turn a parse failure into a statement
    about which figures exist, and would make this module depend on the thing it
    is meant to be independent of. An id that is well-formed but unknown is a
    well-formed reference; the compiler raises ``UnknownMember`` for it, exactly
    as it does for an id that arrives in a query.
    """

    member_id: str
    position: int

    def __post_init__(self) -> None:
        if _MEMBER_ID.fullmatch(self.member_id) is None:
            raise MalformedMemberReference(self.position)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class Negation(Expr):
    """Unary minus."""

    operand: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.operand, "number", _NEGATION_RULE)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class Arithmetic(Expr):
    """``+``, ``-``, ``*`` or ``/`` over two numbers."""

    operator: ArithmeticOperator
    left: Expr
    right: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.left, "number", _ARITHMETIC_RULE)
        _require(self.right, "number", _ARITHMETIC_RULE)
        if self.operator == "/" and _is_zero(self.right):
            raise DivisionByZero(self.right.position)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class Comparison(Expr):
    """``=``, ``!=``, ``<``, ``<=``, ``>`` or ``>=`` over two numbers."""

    operator: ComparisonOperator
    left: Expr
    right: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.left, "number", _COMPARISON_RULE)
        _require(self.right, "number", _COMPARISON_RULE)

    @property
    def result_type(self) -> ExprType:
        return "boolean"


@dataclass(frozen=True, slots=True)
class Logical(Expr):
    """``AND`` or ``OR`` over two yes/no answers."""

    operator: LogicalOperator
    left: Expr
    right: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.left, "boolean", _LOGICAL_RULE)
        _require(self.right, "boolean", _LOGICAL_RULE)

    @property
    def result_type(self) -> ExprType:
        return "boolean"


@dataclass(frozen=True, slots=True)
class LogicalNot(Expr):
    """``NOT`` over a yes/no answer."""

    operand: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.operand, "boolean", _NOT_RULE)

    @property
    def result_type(self) -> ExprType:
        return "boolean"


@dataclass(frozen=True, slots=True)
class SafeDivide(Expr):
    """``SAFE_DIV(a, b)`` — no value when ``b`` is zero or has no value."""

    numerator: Expr
    denominator: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.numerator, "number", _SAFE_DIV_RULE)
        _require(self.denominator, "number", _SAFE_DIV_RULE)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class Conditional(Expr):
    """``IF(condition, a, b)``."""

    condition: Expr
    when_true: Expr
    when_false: Expr
    position: int

    def __post_init__(self) -> None:
        _require(self.condition, "boolean", _CONDITION_RULE)
        _require(self.when_true, "number", _BRANCH_RULE)
        _require(self.when_false, "number", _BRANCH_RULE)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class PercentChange(Expr):
    """``PCT_CHANGE(a, GRAIN)`` — the change on the period before, relative to it.

    ``grain`` is the formula's own declaration of what "the period before" is
    (D-195) and is never inherited from the query: a measure whose meaning
    depended on the question it was asked in could not be certified, because two
    widgets would show two different figures under one approved label.
    """

    operand: Expr
    grain: PeriodGrain
    position: int

    def __post_init__(self) -> None:
        _require(self.operand, "number", _PCT_CHANGE_RULE)

    @property
    def result_type(self) -> ExprType:
        return "number"


@dataclass(frozen=True, slots=True)
class Lag(Expr):
    """``LAG(a, n, GRAIN)`` — the value of ``a`` ``n`` periods of ``GRAIN`` earlier.

    ``n`` is a whole number held on the node, never an expression: the number of
    periods decides how far back the compiler has to widen the query window, and
    a window cannot be widened by an amount that is only known per row. The bound
    on ``n`` here is the language's structural ceiling; what a deployment will
    actually answer is :func:`max_lag_periods`, applied by :func:`parse`.
    """

    operand: Expr
    periods: int
    grain: PeriodGrain
    position: int

    def __post_init__(self) -> None:
        _require(self.operand, "number", _LAG_VALUE_RULE)
        if not 1 <= self.periods <= MAX_LAG_PERIODS:
            raise WrongArgumentType(self.position, _LAG_PERIODS_RULE)

    @property
    def result_type(self) -> ExprType:
        return "number"


# --- walking the tree -------------------------------------------------------------------------


def _sub_expressions(node: Expr) -> tuple[Expr, ...]:
    """Every :class:`Expr` held in ``node``'s fields, in declaration order.

    Structural on purpose. The alternative — each node listing its operands —
    is a second place to keep in step with the fields, and the cost of it
    falling behind is a figure read without an authorization check.
    """
    found: list[Expr] = []
    for spec in fields(cast("DataclassInstance", node)):
        value = getattr(node, spec.name)
        if isinstance(value, Expr):
            found.append(value)
        elif isinstance(value, tuple):
            found.extend(
                item for item in cast("tuple[object, ...]", value) if isinstance(item, Expr)
            )
    return tuple(found)


def walk(node: Expr) -> Iterator[Expr]:
    """Every node of the tree, root first, then left to right.

    Iterative, so a tree at :data:`MAX_DEPTH` — or one built by hand past it —
    costs one list, not one interpreter frame per level.
    """
    pending: list[Expr] = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.children))


def referenced_members(node: Expr) -> tuple[str, ...]:
    """Every figure id the formula names, de-duplicated, in the order written.

    This is the authorization surface of a calculated measure: the caller must
    hold a binding for each of these before the formula may be computed. It is
    a complete account of the tree — the walk is structural (:func:`walk`), so
    there is no node shape it can silently skip.
    """
    found: dict[str, None] = {}
    for current in walk(node):
        if isinstance(current, MemberReference):
            found.setdefault(current.member_id, None)
    return tuple(found)


def declared_grain(node: Expr) -> PeriodGrain | None:
    """The ONE period grain this formula compares over, or ``None`` for no comparison.

    D-195: a calculated measure declares its grain, so a formula that names two
    is :class:`MixedPeriodGrains` rather than a figure whose meaning depends on
    which term a reader looks at. ``None`` means the formula names no period
    function at all and is therefore answerable at any grain.
    """
    found: PeriodGrain | None = None
    for current in walk(node):
        if not isinstance(current, PercentChange | Lag):
            continue
        if found is None:
            found = current.grain
        elif current.grain != found:
            raise MixedPeriodGrains(current.position, found, current.grain)
    return found


def period_offsets(node: Expr) -> tuple[int, ...]:
    """Every period the formula reads, counted in grains BACK from the query's own.

    ``0`` — the query's own period — is always present, so the window a caller
    asked for is always part of what gets scanned even when every figure in the
    formula is lagged. A nested lag COMPOSES: ``LAG(PCT_CHANGE([m:a], MONTH), 2,
    MONTH)`` reads two and three months back, never one and two, which is the
    whole-period-out error D-195 names.

    Iterative, like :func:`walk`, and it visits a shared operand once per offset
    rather than once per node — which is exactly the point: the same figure read
    at two periods is two aggregates.
    """
    found: set[int] = {0}
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


def depth(node: Expr) -> int:
    """The height of the tree, counting ``node`` itself as 1.

    Iterative for the same reason as :func:`walk`: measuring how deep a tree is
    must not itself depend on how deep it is.
    """
    heights: dict[int, int] = {}
    pending: list[tuple[Expr, bool]] = [(node, False)]
    while pending:
        current, measured = pending.pop()
        if measured:
            kids = current.children
            heights[id(current)] = 1 + max((heights[id(kid)] for kid in kids), default=0)
            continue
        pending.append((current, True))
        pending.extend((kid, False) for kid in current.children)
    return heights[id(node)]


# --- helpers ----------------------------------------------------------------------------------

#: The lexical shape of a catalogue id: lower-case snake_case tokens joined with
#: dots, as ``app/domain/bi/catalogue/members.py`` documents. Written with
#: explicit character classes rather than ``\w``/``\d``, which in Python match
#: letters and digits from every script — a Cyrillic lookalike inside a figure
#: reference is refused here rather than carried into the catalogue lookup.
_MEMBER_ID: Final = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")
_MEMBER_REFERENCE: Final = re.compile(r"\[m:([a-z0-9_]+(?:\.[a-z0-9_]+)*)\]")
_NUMBER: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _require(node: Expr, expected: ExprType, rule: str) -> None:
    if node.result_type != expected:
        raise WrongArgumentType(node.position, rule)


def _is_zero(node: Expr) -> bool:
    """Whether a node is a zero written in the formula, sign and all."""
    while isinstance(node, Negation):
        node = node.operand
    return isinstance(node, NumberLiteral) and node.value == 0


def _excerpt(source: str, position: int) -> str:
    """A bounded window on the source, mapped onto the language's own alphabet.

    The result is drawn from ``ALPHABET | {REDACTION}`` and nothing else, so an
    excerpt can carry nothing a formula could not legitimately contain — no
    quote, no backtick, no control byte, no character from another script.
    """
    window = source[position : position + MAX_EXCERPT_LENGTH]
    return "".join(character if character in ALPHABET else REDACTION for character in window)


def _echo(value: str, limit: int) -> str:
    """``value`` when it is short enough to repeat back, else a placeholder."""
    return value if len(value) <= limit else "(withheld: too long to show)"


def _listed(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --- tokens -----------------------------------------------------------------------------------

_WHITESPACE: Final = " \t\n\r"
_TWO_CHARACTER_OPERATORS: Final = ("!=", "<=", ">=")
_ONE_CHARACTER_OPERATORS: Final = "+-*/()=<>,"
_KEYWORDS: Final[Mapping[str, str]] = MappingProxyType({"AND": "and", "OR": "or", "NOT": "not"})


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    position: int
    lexeme: str
    number: Decimal | None = None


def _tokenize(source: str) -> tuple[_Token, ...]:  # noqa: PLR0912 - one branch per token shape
    """Source text to tokens, refusing anything outside the language as it goes."""
    tokens: list[_Token] = []
    members: dict[str, None] = {}
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character in _WHITESPACE:
            index += 1
            continue
        if character == "[":
            reference = _MEMBER_REFERENCE.match(source, index)
            if reference is None:
                raise MalformedMemberReference(index)
            member_id = reference.group(1)
            members.setdefault(member_id, None)
            if len(members) > MAX_MEMBER_REFERENCES:
                raise TooManyMemberReferences(index)
            tokens.append(_Token("member", index, member_id))
            index = reference.end()
            continue
        if "0" <= character <= "9":
            number = _NUMBER.match(source, index)
            if number is None:
                raise UnexpectedCharacter(index)
            tokens.append(_Token("number", index, number.group(), Decimal(number.group())))
            index = number.end()
            continue
        if _NAME.match(character) is not None:
            name = _NAME.match(source, index)
            if name is None:
                raise UnexpectedCharacter(index)
            word = name.group()
            tokens.append(_Token(_KEYWORDS.get(word.upper(), "name"), index, word))
            index = name.end()
            continue
        pair = source[index : index + 2]
        if pair in _TWO_CHARACTER_OPERATORS:
            tokens.append(_Token(pair, index, pair))
            index += 2
            continue
        if character in _ONE_CHARACTER_OPERATORS:
            tokens.append(_Token(character, index, character))
            index += 1
            continue
        raise UnexpectedCharacter(index)
    tokens.append(_Token("end", length, ""))
    return tuple(tokens)


# --- the parser -------------------------------------------------------------------------------

#: Binding power per infix operator, loosest first. Left-associative: the right
#: operand is parsed at the operator's OWN power, so an operator of equal power
#: closes the current node instead of nesting inside it.
_INFIX: Final[Mapping[str, int]] = MappingProxyType(
    {
        "or": 1,
        "and": 2,
        "=": 4,
        "!=": 4,
        "<": 4,
        "<=": 4,
        ">": 4,
        ">=": 4,
        "+": 5,
        "-": 5,
        "*": 6,
        "/": 6,
    }
)
_COMPARISONS: Final = frozenset({"=", "!=", "<", "<=", ">", ">="})
#: ``NOT`` binds looser than a comparison (so ``NOT a > b`` negates the whole
#: comparison) and tighter than ``AND`` (so ``NOT a AND b`` negates only ``a``).
_NOT_POWER: Final = 3
#: Unary minus binds tighter than every infix operator.
_NEGATION_POWER: Final = 7


@dataclass(slots=True)
class _Parser:
    """Precedence-climbing over a finished token list.

    ``level`` counts this parser's own descent so a formula of nothing but
    opening brackets is refused by name at :data:`MAX_DEPTH` rather than by the
    interpreter running out of stack. It does NOT measure the finished tree —
    a left-associative chain is built in a loop, one level deep — which is why
    :func:`parse` measures the tree separately afterwards.
    """

    tokens: tuple[_Token, ...]
    index: int = 0
    level: int = 0

    def peek(self) -> _Token:
        return self.tokens[self.index]

    def advance(self) -> _Token:
        token = self.tokens[self.index]
        if token.kind != "end":
            self.index += 1
        return token

    def expression(self, minimum_power: int) -> Expr:
        self.level += 1
        if self.level > MAX_DEPTH:
            raise ExpressionTooDeep(self.peek().position)
        try:
            return self._climb(minimum_power)
        finally:
            self.level -= 1

    def _climb(self, minimum_power: int) -> Expr:
        left = self.prefix()
        while True:
            token = self.peek()
            power = _INFIX.get(token.kind)
            if power is None or power <= minimum_power:
                return left
            self.advance()
            right = self.expression(power)
            left = _infix_node(token, left, right)
            if token.kind in _COMPARISONS and self.peek().kind in _COMPARISONS:
                raise UnexpectedToken(self.peek().position, _CHAINED_COMPARISON_RULE)

    def prefix(self) -> Expr:  # noqa: PLR0911 - one return per leading shape
        token = self.advance()
        if token.kind == "number":
            value = token.number if token.number is not None else Decimal(0)
            return NumberLiteral(value=value, position=token.position)
        if token.kind == "member":
            return MemberReference(member_id=token.lexeme, position=token.position)
        if token.kind == "-":
            return Negation(operand=self.expression(_NEGATION_POWER), position=token.position)
        if token.kind == "not":
            return LogicalNot(operand=self.expression(_NOT_POWER), position=token.position)
        if token.kind == "(":
            inner = self.expression(0)
            if self.peek().kind != ")":
                raise UnbalancedParenthesis(self.peek().position)
            self.advance()
            return inner
        if token.kind == "name":
            return self.call(token)
        if token.kind == "end":
            raise UnexpectedEndOfExpression(token.position)
        raise UnexpectedToken(token.position, _OPERAND_RULE)

    def call(self, token: _Token) -> Expr:
        name = token.lexeme.upper()
        arity = _FUNCTION_ARITY.get(name)
        if arity is None:
            raise UnknownFunction(token.position, _echo(token.lexeme, _NAME_ECHO_LIMIT))
        if self.peek().kind != "(":
            raise UnexpectedToken(self.peek().position, _FUNCTION_CALL_RULE)
        self.advance()
        if name in _GRAIN_FUNCTIONS:
            return self.time_call(token, name, arity)
        arguments = self.arguments(token, name, arity)
        return _function_node(token, name, arguments)

    def time_call(self, token: _Token, name: str, arity: int) -> Expr:
        """``PCT_CHANGE(value, GRAIN)`` / ``LAG(value, periods, GRAIN)``.

        Parsed POSITIONALLY rather than through :meth:`arguments`, because the
        last input is a period WORD and not an expression — and because the grain
        has to be the last input rather than merely present somewhere, so a
        formula reads in one order and a reviewer compares two formulas without
        re-deriving which argument was which.

        A call written the old way, with no period at all, is refused with the
        whole rule (:data:`_TIME_FUNCTION_RULES`) rather than a bare count: the
        period is part of what the measure means, not a forgotten argument.
        """
        given: list[Expr] = []
        grain: PeriodGrain | None = None
        for index in range(arity):
            if index and self.peek().kind != ",":
                raise WrongArgumentType(token.position, _TIME_FUNCTION_RULES[name])
            if index:
                self.advance()
            if index == arity - 1:
                grain = self.grain_word(name)
            else:
                given.append(self.expression(0))
        if self.peek().kind == ",":
            raise WrongArgumentCount(token.position, name, arity, arity + 1)
        if self.peek().kind != ")":
            raise UnbalancedParenthesis(self.peek().position)
        self.advance()
        assert grain is not None  # noqa: S101 - every grain function has arity >= 1
        return _time_node(token, name, given, grain)

    def grain_word(self, name: str) -> PeriodGrain:
        """One of :data:`GRAIN_WORDS`, consumed as a bare word."""
        word = self.peek()
        if word.kind != "name":
            raise WrongArgumentType(word.position, _TIME_FUNCTION_RULES[name])
        self.advance()
        grain = GRAIN_WORDS.get(word.lexeme.upper())
        if grain is None:
            raise UnknownPeriodGrain(word.position, _echo(word.lexeme, _NAME_ECHO_LIMIT))
        return grain

    def arguments(self, token: _Token, name: str, arity: int) -> list[Expr]:
        given: list[Expr] = []
        if self.peek().kind != ")":
            given.append(self.expression(0))
            while self.peek().kind == ",":
                self.advance()
                given.append(self.expression(0))
                if len(given) > _MAX_ARGUMENTS:
                    raise WrongArgumentCount(token.position, name, arity, len(given))
        if self.peek().kind != ")":
            raise UnbalancedParenthesis(self.peek().position)
        self.advance()
        if len(given) != arity:
            raise WrongArgumentCount(token.position, name, arity, len(given))
        return given


def _infix_node(token: _Token, left: Expr, right: Expr) -> Expr:
    if token.kind in _COMPARISONS:
        operator = cast("ComparisonOperator", token.kind)
        return Comparison(operator=operator, left=left, right=right, position=left.position)
    if token.kind in {"and", "or"}:
        connective = cast("LogicalOperator", token.kind)
        return Logical(operator=connective, left=left, right=right, position=left.position)
    arithmetic = cast("ArithmeticOperator", token.kind)
    return Arithmetic(operator=arithmetic, left=left, right=right, position=left.position)


def _function_node(token: _Token, name: str, given: list[Expr]) -> Expr:
    if name == "SAFE_DIV":
        return SafeDivide(numerator=given[0], denominator=given[1], position=token.position)
    # ``IF`` is the only other function whose inputs are all expressions; the two
    # period functions are built by :func:`_time_node`.
    return Conditional(
        condition=given[0],
        when_true=given[1],
        when_false=given[2],
        position=token.position,
    )


def _time_node(token: _Token, name: str, given: list[Expr], grain: PeriodGrain) -> Expr:
    if name == "PCT_CHANGE":
        return PercentChange(operand=given[0], grain=grain, position=token.position)
    return Lag(operand=given[0], periods=_periods(given[1]), grain=grain, position=token.position)


def _periods(node: Expr) -> int:
    """``LAG``'s second input as a whole number, or a named refusal.

    A negated literal is refused here rather than turned into a negative lag:
    looking FORWARD is a different question from looking back, and answering it
    by accident would quietly read a period the query window never asked for.
    """
    if not isinstance(node, NumberLiteral) or node.value != node.value.to_integral_value():
        raise WrongArgumentType(node.position, _LAG_PERIODS_RULE)
    return int(node.value)


# --- the entry point --------------------------------------------------------------------------


def parse(source: str, *, retention_days: int | None) -> Expr:
    """Parse a calculated-measure formula, or raise a named :class:`ExpressionError`.

    The result is guaranteed to be a NUMBER-typed tree no deeper than
    :data:`MAX_DEPTH` naming no more than :data:`MAX_MEMBER_REFERENCES` distinct
    figures, every one of which :func:`referenced_members` reports, comparing over
    at most one period grain (:func:`declared_grain`). It is not guaranteed that
    those figures exist, or that the caller may read them: the catalogue lookup
    and the authorization walk both happen afterwards, on the ids this function
    hands back.

    ``retention_days`` is how long the deployment keeps daily history
    (``BI_DAILY_RETENTION_DAYS``) and is what bounds a period comparison (D-196,
    :func:`max_lag_periods`). It is a REQUIRED keyword with no default, and
    ``None`` means "do not apply that bound" — the two readings are genuinely
    different callers and neither may be reached by forgetting to say which:

    * a value is what the WRITE path passes, so a formula asking for history the
      marts do not hold is refused where a person can fix it;
    * ``None`` is what an AUTHORIZATION walk passes, because whether an identity
      may read the figures a stored formula names must not change when a
      deployment shortens its retention window — that would make a measure
      silently vanish from its own author's list.
    """
    try:
        return _parse(source, retention_days)
    except ExpressionError as error:
        error.attach_source(source)
        raise


def _parse(source: str, retention_days: int | None) -> Expr:
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionTooLong(MAX_EXPRESSION_LENGTH)
    if not source.strip(_WHITESPACE):
        raise EmptyExpression()
    parser = _Parser(tokens=_tokenize(source))
    root = parser.expression(0)
    trailing = parser.peek()
    if trailing.kind != "end":
        raise TrailingContent(trailing.position)
    if depth(root) > MAX_DEPTH:
        raise ExpressionTooDeep(root.position)
    if root.result_type != "number":
        raise NonNumericExpression(root.position)
    grain = declared_grain(root)
    if grain is not None and retention_days is not None:
        allowed = max_lag_periods(retention_days, grain)
        reach = max(period_offsets(root))
        if reach > allowed:
            raise LagBeyondRetention(
                _first_period_node(root).position, grain, retention_days, allowed
            )
    return root


def _first_period_node(node: Expr) -> Expr:
    """The leftmost period function, which is where a reader looks for the reach."""
    for current in walk(node):
        if isinstance(current, PercentChange | Lag):
            return current
    return node
