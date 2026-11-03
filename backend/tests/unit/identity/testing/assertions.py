"""Approval/request history comparisons use no numeric or time tolerance."""

from decimal import Decimal

from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(Decimal(0), "approval history", "identity/service/authorization.py")


def assert_refused_approval_unchanged(actual: object, before: object) -> None:
    TOLERANCE.assert_exact(
        actual, before, because="Approvals: refused operation changed history or authority"
    )


def assert_sealed_approval_unchanged(actual: object, sealed: object) -> None:
    TOLERANCE.assert_exact(
        actual, sealed, because="Approvals: a decided template/request was rewritten"
    )


def assert_maker_checker(maker: object, checker: object) -> None:
    assert checker is not None and checker != maker, (
        f"Approvals: maker {maker!r} and checker {checker!r} must be distinct officers"
    )
