"""A domain failure must name the violated contract and retain the evidence."""

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

import pytest

from tests.icaap.testing.assertions import (
    assert_distinct_reviewers as assert_icaap_reviewers,
)
from tests.icaap.testing.assertions import assert_frozen_version_unchanged
from tests.identity.testing.assertions import assert_maker_checker, assert_sealed_approval_unchanged
from tests.reporting.testing.assertions import (
    assert_distinct_reviewers,
    assert_filed_record_unchanged,
)
from tests.support.assertions import TolerancePolicy


@pytest.mark.parametrize(
    ("assertion", "domain"),
    [
        (assert_filed_record_unchanged, "Filing"),
        (assert_frozen_version_unchanged, "ICAAP"),
        (assert_sealed_approval_unchanged, "Approvals"),
    ],
)
def test_sealed_evidence_refuses_changed_content(
    assertion: Callable[[object, object], None], domain: str
) -> None:
    original = {"digest": "a" * 64, "version": 1}
    assertion(dict(original), original)
    with pytest.raises(AssertionError, match=domain):
        assertion({**original, "digest": "b" * 64}, original)


@pytest.mark.parametrize("assertion", [assert_distinct_reviewers, assert_icaap_reviewers])
def test_reviewers_must_be_distinct_and_not_the_author(
    assertion: Callable[[dict[int, UUID], UUID | None], None],
) -> None:
    maker, reviewer, validator = (UUID(int=n) for n in range(1, 4))
    assertion({2: reviewer, 3: validator}, maker)
    with pytest.raises(AssertionError, match="own report"):
        assertion({2: maker}, maker)
    with pytest.raises(AssertionError, match="multiple"):
        assertion({2: reviewer, 3: reviewer}, maker)


def test_approval_checker_must_exist_and_differ_from_maker() -> None:
    assert_maker_checker("maker", "checker")
    for checker in (None, "maker"):
        with pytest.raises(AssertionError, match="distinct officers"):
            assert_maker_checker("maker", checker)


def test_tolerance_policy_is_absolute_in_declared_units_and_refuses_nonfinite() -> None:
    policy = TolerancePolicy(Decimal("0.01"), "test units", "synthetic policy example")
    policy.assert_close(Decimal("1.01"), Decimal(1), because="boundary")
    for actual in (Decimal("1.0101"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(AssertionError, match="test units"):
            policy.assert_close(actual, Decimal(1), because="boundary")


@pytest.mark.parametrize("tolerance", [Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity")])
def test_invalid_tolerance_policy_is_refused(tolerance: Decimal) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        TolerancePolicy(tolerance, "test units", "synthetic policy example")
