"""Credit reconciliation and comparisons, without classification arithmetic."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.capital.loan_classification import ClassificationResult
from app.domain.credit.concentration_monitor import ConcentrationMonitorResult
from tests.support.assertions import TolerancePolicy


@dataclass(frozen=True)
class CreditTolerance:
    """One policy with separate fraction and percentage-point quanta."""

    fractions: TolerancePolicy = TolerancePolicy(
        Decimal("0.000001"),
        "ratio fraction",
        "loan_classification.py: six-decimal HALF_EVEN NPL fraction",
    )
    percentages: TolerancePolicy = TolerancePolicy(
        Decimal("0.000001"),
        "percentage points",
        "credit/concentration_monitor.py: six-decimal HALF_EVEN capital share",
    )


TOLERANCE = CreditTolerance()


def assert_credit_invariants(result: ClassificationResult) -> None:
    assert sum(bucket.count for bucket in result.buckets) == len(result.loans), (
        f"Credit: grade counts do not reconcile to the classified facilities: {result}"
    )
    assert result.total_exposure_ghs == (
        result.performing_exposure_ghs + result.npl_exposure_ghs + result.unclassified_exposure_ghs
    ), f"Credit: performing, NPL and unclassified balances do not reconcile: {result}"
    assert sum((b.exposure_ghs for b in result.buckets), Decimal(0)) == result.total_exposure_ghs, (
        f"Credit: grade balances do not reconcile to the book: {result}"
    )
    assert (
        sum((b.provision_required_ghs for b in result.buckets), Decimal(0))
        == result.total_provision_required_ghs
    ), f"Credit: grade provisions do not reconcile to booked requirement: {result}"
    assert all(
        Decimal(0) <= loan.provision_required_ghs <= loan.exposure_ghs for loan in result.loans
    ), f"Credit: provision is outside its facility exposure: {result.loans}"
    assert result.npl_ratio.is_finite() and 0 <= result.npl_ratio <= 1, (
        f"Credit: NPL fraction is outside [0,1]: {result.npl_ratio}"
    )


def assert_credit_ratio_unchanged(
    after: ClassificationResult, before: ClassificationResult
) -> None:
    TOLERANCE.fractions.assert_close(
        after.npl_ratio, before.npl_ratio, because="Credit: scaling changed NPL fraction"
    )


def assert_concentration_reconciles(result: ConcentrationMonitorResult) -> None:
    for dimension in result.dimensions:
        assert (
            sum((b.exposure_ghs for b in dimension.buckets), Decimal(0))
            == dimension.stated_exposure_ghs
        ), f"Credit {dimension.dimension}: connected buckets do not reconcile to stated exposure"
        assert 0 <= dimension.stated_exposure_ghs <= result.total_book_ghs, (
            f"Credit {dimension.dimension}: stated exposure exceeds the book"
        )
