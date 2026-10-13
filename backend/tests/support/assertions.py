"""Assertion primitives; each feature owns its comparison policy and meaning."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TolerancePolicy:
    """Absolute tolerance in the compared quantity's units; no implicit relative error."""

    absolute: Decimal
    unit: str
    source: str

    def __post_init__(self) -> None:
        if not self.absolute.is_finite() or self.absolute < 0:
            raise ValueError("Absolute tolerance must be finite and non-negative")

    def assert_exact(self, actual: object, expected: object, *, because: str) -> None:
        if self.absolute != 0:
            raise ValueError("Workflow evidence requires a zero-tolerance policy")
        assert_unchanged(actual, expected, because=f"{because} ({self.source})")

    def assert_close(self, actual: Decimal, expected: Decimal, *, because: str) -> None:
        assert actual.is_finite() and expected.is_finite(), f"{because}: non-finite {self.unit}"
        assert abs(actual - expected) <= self.absolute, (
            f"{because}: {actual} != {expected} {self.unit}; "
            f"absolute tolerance {self.absolute} ({self.source})"
        )


def assert_unchanged(actual: object, before: object, *, because: str) -> None:
    assert actual == before, (
        f"{because}: protected evidence changed\nbefore={before!r}\nafter={actual!r}"
    )


def assert_workflow_state(actual: object, expected: object, *, domain: str) -> None:
    assert actual == expected, f"{domain}: persisted state {actual!r}; model expected {expected!r}"
