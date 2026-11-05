"""``bi_fact_target``: the bank's budget, PRE-MATCHED to its actual (D-064).

The founder's decision moved one rule into the builder — **a target declared
for exactly this scope wins, the bank-wide target is the fallback, and the two
are NEVER summed** — so that rule gets its own table of cases here, over all
four states a scope can be in, plus the end-to-end proof that what the builder
writes is what the catalogue's variants then read.

The figures are worked from the canonical fixture's own amounts
(``tests/support/factories/canonical.py``): seven converted loans totalling 84 850 000
in the reporting currency at 2026-06-30.

This module's other-suite cases live in ``test_target_marts_integration.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.bi.domain.catalogue.targets import TARGET_VERSION
from app.bi.service import mart_builder
from app.bi.service.mart_builder import (
    BANK_WIDE_SCOPE,
    DeclaredTarget,
    resolve_target_for_scope,
    target_window,
)
from tests.bi.service.test_target_marts_integration import LOANS, QUARTER_END


def _declared(scope_dimension: str, scope_value: str, value: str) -> DeclaredTarget:
    return DeclaredTarget(
        measure_id=LOANS,
        scope_dimension=scope_dimension,
        scope_value=scope_value,
        version=TARGET_VERSION,
        grain="quarter",
        time_behaviour="stock",
        period_start=date(2026, 4, 1),
        period_end=QUARTER_END,
        value=Decimal(value),
    )


BANK_WIDE = _declared("", "", "100")
BRANCH_ONE = _declared("branch.code", "BR-001", "40")
SCOPE_ONE = ("branch.code", "BR-001")
#: (case, candidates, scope asked for, expected value, expected basis)
_Case = tuple[str, list[DeclaredTarget], tuple[str, str], str | None, str | None]
SCOPE_CASES: tuple[_Case, ...] = (
    ("exact only", [BRANCH_ONE], SCOPE_ONE, "40", "exact"),
    ("bank-wide only", [BANK_WIDE], SCOPE_ONE, "100", "bank_wide"),
    ("both present", [BANK_WIDE, BRANCH_ONE], SCOPE_ONE, "40", "exact"),
    ("neither", [], SCOPE_ONE, None, None),
    ("bank-wide asked, declared", [BANK_WIDE, BRANCH_ONE], BANK_WIDE_SCOPE, "100", "exact"),
    ("bank-wide asked, only scoped", [BRANCH_ONE], BANK_WIDE_SCOPE, None, None),
)


@pytest.mark.parametrize(
    ("case", "candidates", "scope", "value", "basis"),
    SCOPE_CASES,
    ids=[case[0] for case in SCOPE_CASES],
)
def test_exact_scope_wins_bank_wide_is_the_fallback(
    case: str,
    candidates: list[DeclaredTarget],
    scope: tuple[str, str],
    value: str | None,
    basis: str | None,
) -> None:
    resolved = resolve_target_for_scope(candidates, scope=scope)
    if value is None:
        assert resolved is None, case
        return
    assert resolved is not None, case
    assert resolved.declared.value == Decimal(value), case
    assert resolved.basis == basis, case


def test_the_two_bases_are_never_summed() -> None:
    """The property, stated separately from the cases that exercise it.

    Whatever the candidate set, at most ONE target comes back, and its value is
    one of the declared ones — never their sum. That is what makes a bank-wide
    target beside a per-branch one impossible to double-count at any grain,
    rather than merely unlikely.
    """
    for scope in (SCOPE_ONE, BANK_WIDE_SCOPE, ("branch.code", "BR-999")):
        resolved = resolve_target_for_scope([BANK_WIDE, BRANCH_ONE], scope=scope)
        if resolved is None:
            continue
        assert resolved.declared.value in (BANK_WIDE.value, BRANCH_ONE.value)
        assert resolved.declared.value != BANK_WIDE.value + BRANCH_ONE.value


def test_a_restated_target_is_the_later_statement_not_a_second_one() -> None:
    first, second = _declared("", "", "100"), _declared("", "", "120")
    resolved = resolve_target_for_scope([first, second], scope=BANK_WIDE_SCOPE)
    assert resolved is not None and resolved.declared.value == Decimal("120")


def test_a_period_that_is_not_its_grains_last_day_names_no_window() -> None:
    """A quarterly target dated 15 March is a mistake about which quarter is meant."""
    assert target_window(date(2026, 3, 31), "quarter") == (date(2026, 1, 1), date(2026, 3, 31))
    assert target_window(date(2026, 3, 15), "quarter") is None
    assert target_window(date(2026, 6, 30), "half_year") == (date(2026, 1, 1), date(2026, 6, 30))
    assert target_window(date(2026, 12, 31), "year") == (date(2026, 1, 1), date(2026, 12, 31))
    assert target_window(date(2026, 2, 28), "month") == (date(2026, 2, 1), date(2026, 2, 28))
    assert target_window(date(2026, 6, 30), "fortnight") is None


def test_the_targets_register_is_a_build_fingerprint_input() -> None:
    """Without this a re-pushed budget serves the OLD variance out of cache."""
    assert mart_builder.TARGETS_KIND == "performance_targets"
    assert mart_builder.TARGETS_KIND in mart_builder.REFERENCE_KINDS
