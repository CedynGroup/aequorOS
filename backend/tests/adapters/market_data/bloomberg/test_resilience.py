"""Session-management resilience: retry/backoff, per-endpoint rate limiting."""

from __future__ import annotations

import pytest

from app.adapters.market_data.bloomberg.resilience import (
    ConnectionPoolConfig,
    RetryPolicy,
    TokenBucketRateLimiter,
    endpoints_of,
    retry_with_backoff,
)
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)


def _error(code: BankFacingErrorCode) -> MarketDataError:
    return MarketDataError(
        render_bank_facing(code, vendor="Bloomberg", timestamp="n/a", scope="X"),
        internal_detail="synthetic",
    )


def test_retry_recovers_after_transient_failures() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _error(BankFacingErrorCode.VENDOR_UNAVAILABLE)
        return "ok"

    result = retry_with_backoff(
        flaky,
        RetryPolicy(max_attempts=3, base_delay_seconds=1),
        sleep=sleeps.append,
        jitter=lambda ceiling: ceiling,
    )
    assert result == "ok"
    assert attempts["n"] == 3
    # Full-jitter ceiling doubles: attempt 1 -> 1s, attempt 2 -> 2s.
    assert sleeps == [1.0, 2.0]


def test_retry_reraises_after_exhausting_attempts() -> None:
    sleeps: list[float] = []

    def always_down() -> str:
        raise _error(BankFacingErrorCode.RATE_LIMITED)

    with pytest.raises(MarketDataError) as excinfo:
        retry_with_backoff(
            always_down,
            RetryPolicy(max_attempts=2, base_delay_seconds=0.5),
            sleep=sleeps.append,
            jitter=lambda ceiling: ceiling,
        )
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.RATE_LIMITED
    # One sleep between the two attempts; none after the final failure.
    assert sleeps == [0.5]


def test_retry_does_not_retry_terminal_errors() -> None:
    calls = {"n": 0}

    def denied() -> str:
        calls["n"] += 1
        raise _error(BankFacingErrorCode.CREDENTIAL_INVALID)

    with pytest.raises(MarketDataError):
        retry_with_backoff(denied, RetryPolicy(max_attempts=5), sleep=lambda _: None)
    assert calls["n"] == 1  # terminal classification is not retried


def test_retry_propagates_non_market_data_errors_immediately() -> None:
    calls = {"n": 0}

    def boom() -> str:
        calls["n"] += 1
        raise RuntimeError("not a vendor error")

    with pytest.raises(RuntimeError):
        retry_with_backoff(boom, RetryPolicy(max_attempts=5), sleep=lambda _: None)
    assert calls["n"] == 1


def test_backoff_ceiling_doubles_and_caps() -> None:
    policy = RetryPolicy(base_delay_seconds=1, max_delay_seconds=4)
    assert policy.backoff_ceiling(1) == 1
    assert policy.backoff_ceiling(2) == 2
    assert policy.backoff_ceiling(3) == 4
    assert policy.backoff_ceiling(4) == 4  # capped


def test_retry_policy_rejects_bad_config() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_burst_then_throttles() -> None:
    clock = _FakeClock()
    waits: list[float] = []
    limiter = TokenBucketRateLimiter(
        capacity=2, refill_per_second=1, clock=clock, sleep=waits.append
    )
    # Two immediate tokens, no wait.
    assert limiter.acquire("bval") == 0.0
    assert limiter.acquire("bval") == 0.0
    # Third must wait one refill period (1 token / 1 per second).
    waited = limiter.acquire("bval")
    assert waited == pytest.approx(1.0)
    assert waits == [pytest.approx(1.0)]


def test_rate_limiter_buckets_are_per_endpoint() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(
        capacity=1, refill_per_second=1, clock=clock, sleep=lambda _: None
    )
    assert limiter.acquire("bval") == 0.0
    # A different endpoint has its own full bucket.
    assert limiter.acquire("refdata") == 0.0


def test_rate_limiter_refills_over_time() -> None:
    clock = _FakeClock()
    limiter = TokenBucketRateLimiter(
        capacity=1, refill_per_second=2, clock=clock, sleep=lambda _: None
    )
    assert limiter.acquire("bval") == 0.0
    clock.now = 0.5  # half a second -> one token refilled at 2/s
    assert limiter.acquire("bval") == 0.0


def test_rate_limiter_rejects_request_larger_than_capacity() -> None:
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1, sleep=lambda _: None)
    with pytest.raises(ValueError, match="exceeds bucket capacity"):
        limiter.acquire("bval", tokens=3)


def test_connection_pool_config_validates() -> None:
    config = ConnectionPoolConfig()
    assert config.max_sessions >= 1
    with pytest.raises(ValueError, match="max_sessions"):
        ConnectionPoolConfig(max_sessions=0)
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        ConnectionPoolConfig(request_timeout_seconds=0)


def test_endpoints_of_reads_data_source_with_fallback() -> None:
    specs = [{"data_source": "BVAL"}, {"field": "PX_LAST"}]
    assert endpoints_of(specs, default="refdata") == {"BVAL", "refdata"}
