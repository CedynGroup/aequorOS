"""The validation rule engine (SPEC Part 2). Rules are data; nothing is ever evaluated
as code. Six kinds: ``sum``, ``non_negative``, ``compare``, ``cross_equal``, ``variance``,
``present``.

Figures are exact decimals: a line's value is a decimal string (or, in payloads filed
before decimal lines, an integer), read with :class:`~decimal.Decimal`; a blank line
(``null``) counts as zero, as an empty cell does in the workbook the return comes from.

Outcomes: a rule *passes*, *fails* (an issue of the rule's severity) or is *not checked*
(a warning, when the comparison filing does not exist). ``passed`` counts passing rules,
``failed`` counts failing error rules, ``warnings`` counts warning issues.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from app.core.errors import Issue

Severity = Literal["error", "warning"]


class SumRule(BaseModel):
    """``target`` equals the sum of ``terms`` less the sum of ``subtract``."""

    id: str
    severity: Severity
    kind: Literal["sum"]
    target: str
    terms: list[str] = Field(default_factory=lambda: [])
    subtract: list[str] = Field(default_factory=lambda: [])
    tolerance: int | float | str = 0
    description: str


class NonNegativeRule(BaseModel):
    id: str
    severity: Severity
    kind: Literal["non_negative"]
    lines: list[str] = Field(min_length=1)
    description: str


class CompareRule(BaseModel):
    id: str
    severity: Severity
    kind: Literal["compare"]
    left: str
    op: Literal[">=", "<=", "=="]
    right: str = Field(description="A line code")
    description: str


class CrossEqualRule(BaseModel):
    id: str
    severity: Severity
    kind: Literal["cross_equal"]
    line: str
    other_return: str
    other_line: str
    tolerance: int | float | str = 0
    description: str


class VarianceRule(BaseModel):
    id: str
    severity: Literal["warning"]
    kind: Literal["variance"]
    line: str
    max_change_pct: int | float = Field(gt=0)
    description: str


class PresentRule(BaseModel):
    """An itemised section carries at least one row (lines coded ``<section>:<row>``)."""

    id: str
    severity: Severity
    kind: Literal["present"]
    section: str
    description: str


Rule = Annotated[
    SumRule | NonNegativeRule | CompareRule | CrossEqualRule | VarianceRule | PresentRule,
    Field(discriminator="kind"),
]
RULES_ADAPTER: TypeAdapter[list[Rule]] = TypeAdapter(list[Rule])


def parse_rules(raw: Sequence[JsonValue]) -> list[Rule]:
    return RULES_ADAPTER.validate_python(list(raw))


def dump_rules(rules: Sequence[Rule]) -> list[JsonValue]:
    dumped: list[JsonValue] = []
    for rule in rules:
        dumped.append(rule.model_dump(mode="json"))  # pyright: ignore[reportAny]
    return dumped


GROUPS: dict[str, tuple[str, str]] = {
    "STR": ("Structure", "Schema, codes, units"),
    "ARI": ("Arithmetic", "Totals and subtotals"),
    "XR": ("Cross-return", "Against other returns"),
    "PER": ("Period", "Reporting date and continuity"),
}
GROUP_ORDER = ("STR", "ARI", "XR", "PER", "OTHER")


def group_of(rule_id: str) -> str:
    """The display group from the rule id prefix: ``BSD2-STR-001`` → ``STR``."""
    for token in rule_id.upper().split("-"):
        if token in GROUPS:
            return token
    return "OTHER"


def group_label(group: str) -> str:
    return GROUPS[group][0] if group in GROUPS else "Other"


def rule_lines(rule: Rule) -> list[str]:
    """Line codes of *this* return that a rule reads (none for a section rule)."""
    if isinstance(rule, SumRule):
        return [rule.target, *rule.terms, *rule.subtract]
    if isinstance(rule, NonNegativeRule):
        return list(rule.lines)
    if isinstance(rule, CompareRule):
        return [rule.left, rule.right]
    if isinstance(rule, PresentRule):
        return []
    return [rule.line]


def decimal_of(value: int | float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def fmt(value: Decimal | float) -> str:
    number = decimal_of(value)
    if number == number.to_integral_value():
        return f"{int(number):,}"
    return f"{number:,.2f}"


@dataclass(frozen=True)
class RuleContext:
    #: Every numeric line, blank lines as zero.
    lines: Mapping[str, Decimal]
    labels: Mapping[str, str]
    unit_label: str
    other_lines: Callable[[str], Mapping[str, Decimal] | None]
    previous_lines: Mapping[str, Decimal] | None
    previous_label: str | None
    reporting_label: str
    #: Every line code the payload carries, numeric or not.
    codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RuleOutcome:
    rule_id: str
    group: str
    status: Literal["passed", "failed", "not_checked"]
    issue: Issue | None


@dataclass
class Evaluation:
    outcomes: list[RuleOutcome] = field(default_factory=lambda: [])

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "passed")

    @property
    def failed(self) -> int:
        return sum(
            1
            for o in self.outcomes
            if o.status == "failed" and o.issue is not None and o.issue.severity == "error"
        )

    @property
    def issues(self) -> list[Issue]:
        return [o.issue for o in self.outcomes if o.issue is not None]


def _pointer(code: str) -> str:
    return "/lines/" + code.replace("~", "~0").replace("/", "~1")


def _label(ctx: RuleContext, code: str) -> str:
    label = ctx.labels.get(code)
    return f"{code} {label}" if label else code


def _missing(rule: Rule, ctx: RuleContext) -> RuleOutcome | None:
    absent = [code for code in rule_lines(rule) if code not in ctx.lines]
    if not absent:
        return None
    why = "is not a number" if absent[0] in ctx.codes else "is missing"
    return RuleOutcome(
        rule.id,
        group_of(rule.id),
        "failed",
        Issue(
            rule_id=rule.id,
            severity=rule.severity,
            pointer=_pointer(absent[0]),
            message=f"Line {absent[0]} {why}, so this rule could not be applied.",
        ),
    )


def _fail(rule: Rule, pointer: str, message: str) -> RuleOutcome:
    return RuleOutcome(
        rule.id,
        group_of(rule.id),
        "failed",
        Issue(rule_id=rule.id, severity=rule.severity, pointer=pointer, message=message),
    )


def _not_checked(rule: Rule, pointer: str, message: str) -> RuleOutcome:
    return RuleOutcome(
        rule.id,
        group_of(rule.id),
        "not_checked",
        Issue(rule_id=rule.id, severity="warning", pointer=pointer, message=message),
    )


def _passed(rule: Rule) -> RuleOutcome:
    return RuleOutcome(rule.id, group_of(rule.id), "passed", None)


def evaluate_rule(rule: Rule, ctx: RuleContext) -> RuleOutcome:
    if isinstance(rule, PresentRule):
        prefix = f"{rule.section}:"
        if any(code.startswith(prefix) for code in ctx.codes):
            return _passed(rule)
        return _fail(rule, "/lines", f"{rule.description}: the filing carries no rows for it.")
    missing = _missing(rule, ctx)
    if missing is not None:
        return missing
    lines = ctx.lines
    if isinstance(rule, SumRule):
        total = sum((lines[t] for t in rule.terms), Decimal(0)) - sum(
            (lines[t] for t in rule.subtract), Decimal(0)
        )
        difference = lines[rule.target] - total
        if abs(difference) > decimal_of(rule.tolerance):
            return _fail(
                rule,
                _pointer(rule.target),
                f"{_label(ctx, rule.target)} is {fmt(lines[rule.target])} but its parts "
                f"come to {fmt(total)} (difference {fmt(difference)}). {rule.description}.",
            )
        return _passed(rule)
    if isinstance(rule, NonNegativeRule):
        negative = [code for code in rule.lines if lines[code] < 0]
        if negative:
            return _fail(
                rule,
                _pointer(negative[0]),
                f"{_label(ctx, negative[0])} is negative ({fmt(lines[negative[0]])})"
                + (
                    f" and {len(negative) - 1} more lines are negative."
                    if len(negative) > 1
                    else "."
                ),
            )
        return _passed(rule)
    if isinstance(rule, CompareRule):
        left = lines[rule.left]
        right = lines[rule.right]
        ok = {">=": left >= right, "<=": left <= right, "==": left == right}[rule.op]
        if not ok:
            right_text = f"{_label(ctx, rule.right)} ({fmt(right)})"
            words = {">=": "at least", "<=": "at most", "==": "equal to"}[rule.op]
            return _fail(
                rule,
                _pointer(rule.left),
                f"{_label(ctx, rule.left)} ({fmt(left)}) should be {words} {right_text}.",
            )
        return _passed(rule)
    if isinstance(rule, CrossEqualRule):
        other = ctx.other_lines(rule.other_return)
        if other is None or rule.other_line not in other:
            return _not_checked(
                rule,
                _pointer(rule.line),
                f"Not checked: no current {rule.other_return} filing for {ctx.reporting_label} "
                f"to compare line {rule.line} with.",
            )
        difference = lines[rule.line] - other[rule.other_line]
        if abs(difference) > decimal_of(rule.tolerance):
            return _fail(
                rule,
                _pointer(rule.line),
                f"{_label(ctx, rule.line)} is {fmt(lines[rule.line])} but {rule.other_return} line "
                f"{rule.other_line} is {fmt(other[rule.other_line])} (difference {fmt(difference)}).",
            )
        return _passed(rule)
    previous = ctx.previous_lines
    if previous is None or rule.line not in previous:
        return _not_checked(
            rule,
            _pointer(rule.line),
            f"Not checked: no current filing for the previous reporting date to compare line "
            f"{rule.line} with.",
        )
    before = previous[rule.line]
    now = lines[rule.line]
    if before == 0:
        change_pct = 0.0 if now == 0 else float("inf")
    else:
        change_pct = float(abs(now - before) / abs(before) * 100)
    if change_pct > rule.max_change_pct:
        moved = "∞" if change_pct == float("inf") else f"{change_pct:.1f}%"
        return _fail(
            rule,
            _pointer(rule.line),
            f"{_label(ctx, rule.line)} moved by {moved} from {ctx.previous_label} "
            f"({fmt(before)} to {fmt(now)}), above the {fmt(rule.max_change_pct)}% threshold.",
        )
    return _passed(rule)


def evaluate(rules: Sequence[Rule], ctx: RuleContext) -> Evaluation:
    result = Evaluation()
    for rule in rules:
        result.outcomes.append(evaluate_rule(rule, ctx))
    return result
