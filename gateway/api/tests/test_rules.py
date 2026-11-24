# pyright: standard
"""The safe rule engine: every kind, pass, fail and not-checked outcomes, and groups."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from app.returns.rules import (
    CompareRule,
    CrossEqualRule,
    NonNegativeRule,
    RuleContext,
    SumRule,
    VarianceRule,
    evaluate,
    group_of,
    parse_rules,
)
from tests.synthetic_definitions import SYNTHETIC_DEFINITIONS, build_rules


def _decimal(values: Mapping[str, int]) -> dict[str, Decimal]:
    return {code: Decimal(value) for code, value in values.items()}


def ctx(
    lines: dict[str, int],
    other: Mapping[str, Mapping[str, int]] | None = None,
    previous: dict[str, int] | None = None,
) -> RuleContext:
    others = {code: _decimal(values) for code, values in (other or {}).items()}
    return RuleContext(
        lines=_decimal(lines),
        labels={"1": "Total"},
        unit_label="",
        other_lines=lambda code: others.get(code),
        previous_lines=None if previous is None else _decimal(previous),
        previous_label="30 Sep 2026",
        reporting_label="31 Oct 2026",
    )


def test_sum_with_tolerance() -> None:
    rule = SumRule(
        id="X-ARI-001",
        severity="error",
        kind="sum",
        target="1",
        terms=["1.1", "1.2"],
        tolerance=1,
        description="",
    )
    assert evaluate([rule], ctx({"1": 10, "1.1": 4, "1.2": 6})).passed == 1
    assert evaluate([rule], ctx({"1": 11, "1.1": 4, "1.2": 6})).passed == 1
    failed = evaluate([rule], ctx({"1": 12, "1.1": 4, "1.2": 6}))
    assert failed.failed == 1
    assert failed.issues[0].pointer == "/lines/1"
    assert "difference 2" in failed.issues[0].message


def test_non_negative() -> None:
    rule = NonNegativeRule(
        id="X-STR-001", severity="error", kind="non_negative", lines=["a", "b"], description=""
    )
    assert evaluate([rule], ctx({"a": 0, "b": 5})).failed == 0
    result = evaluate([rule], ctx({"a": -1, "b": 5}))
    assert result.failed == 1
    assert result.issues[0].pointer == "/lines/a"


def test_compare_operators_use_line_codes() -> None:
    lines = {"1": 10, "2": 10, "3": 5}
    for op, right, ok in (
        (">=", "3", True),
        ("<=", "3", False),
        ("==", "2", True),
        ("==", "3", False),
    ):
        rule = CompareRule(
            id="X-ARI-002",
            severity="error",
            kind="compare",
            left="1",
            op=cast(Any, op),
            right=right,
            description="",
        )
        assert (evaluate([rule], ctx(lines)).failed == 0) is ok, (op, right)


def test_cross_equal_checked_failed_and_not_checked() -> None:
    rule = CrossEqualRule(
        id="X-XR-001",
        severity="error",
        kind="cross_equal",
        line="1",
        other_return="BSD6",
        other_line="9",
        tolerance=0,
        description="",
    )
    assert evaluate([rule], ctx({"1": 7}, {"BSD6": {"9": 7}})).passed == 1
    assert evaluate([rule], ctx({"1": 7}, {"BSD6": {"9": 8}})).failed == 1
    missing = evaluate([rule], ctx({"1": 7}))
    assert missing.passed == 0
    assert missing.failed == 0
    assert missing.issues[0].severity == "warning"
    assert missing.issues[0].message.startswith("Not checked")


def test_variance_is_a_warning() -> None:
    rule = VarianceRule(
        id="X-PER-001",
        severity="warning",
        kind="variance",
        line="1",
        max_change_pct=10,
        description="",
    )
    assert evaluate([rule], ctx({"1": 105}, previous={"1": 100})).passed == 1
    moved = evaluate([rule], ctx({"1": 130}, previous={"1": 100}))
    assert moved.failed == 0
    assert moved.passed == 0
    assert moved.issues[0].severity == "warning"
    assert "30.0%" in moved.issues[0].message
    assert evaluate([rule], ctx({"1": 130})).issues[0].message.startswith("Not checked")


def test_missing_line_fails_the_rule() -> None:
    rule = SumRule(
        id="X-ARI-001",
        severity="error",
        kind="sum",
        target="1",
        terms=["2"],
        tolerance=0,
        description="",
    )
    result = evaluate([rule], ctx({"1": 1}))
    assert result.failed == 1
    assert "missing" in result.issues[0].message


def test_group_from_rule_id_prefix() -> None:
    assert group_of("BSD2-STR-001") == "STR"
    assert group_of("CAR-RWA-ARI-004") == "ARI"
    assert group_of("NPL-MONTHLY-XR-1") == "XR"
    assert group_of("LMT-PER-015") == "PER"
    assert group_of("SOMETHING") == "OTHER"


def test_rules_are_data_and_round_trip() -> None:
    raw = [
        {
            "id": "R-1",
            "severity": "error",
            "kind": "sum",
            "target": "1",
            "terms": ["2"],
            "tolerance": 0,
            "description": "d",
        },
        {
            "id": "R-2",
            "severity": "warning",
            "kind": "variance",
            "line": "1",
            "max_change_pct": 25,
            "description": "d",
        },
    ]
    rules = parse_rules(raw)
    assert [r.kind for r in rules] == ["sum", "variance"]


def test_synthetic_definitions_match_the_design_rule_counts() -> None:
    expected = {
        "DBK-DAILY": 12,
        "BSD1": 16,
        "BSD2": 58,
        "BSD3A": 20,
        "BSD5A": 44,
        "BSD6": 40,
        "BSD7A": 26,
        "BSD8": 24,
        "BSD13": 18,
        "CAR-RWA": 62,
        "LMT": 71,
        "LE-MONTHLY": 33,
        "NPL-MONTHLY": 31,
        "IRRBB-PILOT": 27,
        "ICAAP-REPORT": 9,
    }
    assert {s.code: len(build_rules(s)) for s in SYNTHETIC_DEFINITIONS} == expected
    groups: dict[str, int] = {}
    for rule in build_rules(next(s for s in SYNTHETIC_DEFINITIONS if s.code == "BSD2")):
        groups[group_of(rule.id)] = groups.get(group_of(rule.id), 0) + 1
    assert groups == {"STR": 12, "ARI": 22, "XR": 14, "PER": 10}
