"""ICAAP history is exact, including every superseded sealed version."""

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(Decimal(0), "sealed ICAAP evidence", "icaap/service/freeze.py")


def assert_frozen_version_unchanged(actual: object, sealed: object) -> None:
    TOLERANCE.assert_exact(
        actual, sealed, because="ICAAP: refreeze or return rewrote a sealed version"
    )


def assert_distinct_reviewers(standing: Mapping[int, UUID], maker: UUID | None) -> None:
    assert maker not in standing.values(), f"ICAAP: author {maker} reviewed own report: {standing}"
    assert len(set(standing.values())) == len(standing), (
        f"ICAAP: an officer decided multiple standing review stages: {standing}"
    )


def assert_refused_cycle_unchanged(actual: object, before: object) -> None:
    TOLERANCE.assert_exact(
        actual, before, because="ICAAP: refused command changed workspace or history"
    )


def assert_review_order_independent(actual: object, reordered: object) -> None:
    assert actual == reordered, (
        f"ICAAP: reordered decisions changed review result: {actual!r} != {reordered!r}"
    )
