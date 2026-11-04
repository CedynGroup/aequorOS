"""Exact filing evidence and officer invariants; never calculate a filed figure."""

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(Decimal(0), "workflow evidence", "reporting/domain/review_digest.py")


def assert_filed_record_unchanged(actual: object, sealed: object) -> None:
    TOLERANCE.assert_exact(
        actual, sealed, because="Filing: sealed snapshot and digests are immutable"
    )


def assert_distinct_reviewers(standing: Mapping[int, UUID], maker: UUID | None) -> None:
    assert maker not in standing.values(), f"Filing: maker {maker} approved own report: {standing}"
    assert len(set(standing.values())) == len(standing), (
        f"Filing: one officer occupies multiple standing review stages: {standing}"
    )


def assert_refused_filing_unchanged(actual: object, before: object) -> None:
    TOLERANCE.assert_exact(
        actual, before, because="Filing: refused command changed state or success events"
    )


def assert_review_order_independent(actual: object, reordered: object) -> None:
    assert actual == reordered, (
        f"Filing: reordered decisions changed review result: {actual!r} != {reordered!r}"
    )
