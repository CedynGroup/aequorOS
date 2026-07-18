"""RDP session-management resilience: retry/backoff, per-endpoint rate limiting."""

from __future__ import annotations

import pytest

from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.refinitiv.resilience import (
    ConnectionPoolConfig,
    RetryPolicy,
    TokenBucketRateLimiter,
    retry_with_backoff,
)


def _error(code: BankFacingErrorCode) -> MarketDataError:
    return MarketDataError(
        render_bank_facing(code, vendor="Refinitiv (LSEG)", timestamp="n/a", scope="X"),
        internal_detail="synthetic",
    )


def test_retry_recovers_after_transient_failures() -> None:
    attempts = {"n": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _error(BankFacingErrorCode.RATE_LIMITED)
        return "ok"

    result = retry_with_backoff(
        flaky,
        RetryPolicy(max_attempts=3, base_delay_seconds=1),
        sleep=sleeps.append,
        jitter=lambda ceiling: ceiling,
    )
    assert result == "ok"
    assert sleeps == [1.0, 2.0]


def test_retry_reraises_after_exhausting_attempts() -> None:
    def always_down() -> str:
        raise _error(BankFacingErrorCode.VENDOR_UNAVAILABLE)

    with pytest.raises(MarketDataError) as excinfo:
        retry_with_backoff(
            always_down,
            RetryPolicy(max_attempts=2),
            sleep=lambda _: None,
            jitter=lambda ceiling: ceiling,
        )
    assert excinfo.value.bank_facing.code is BankFacingErrorCode.VENDOR_UNAVAILABLE


def test_retry_does_not_retry_terminal_errors() -> None:
    calls = {"n": 0}

    def denied() -> str:
        calls["n"] += 1
        raise _error(BankFacingErrorCode.SCOPE_NOT_PERMITTED)

    with pytest.raises(MarketDataError):
        retry_with_backoff(denied, RetryPolicy(max_attempts=5), sleep=lambda _: None)
    assert calls["n"] == 1


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
    assert limiter.acquire("rdp-data") == 0.0
    assert limiter.acquire("rdp-data") == 0.0
    assert limiter.acquire("rdp-data") == pytest.approx(1.0)
    assert waits == [pytest.approx(1.0)]


def test_rate_limiter_rejects_request_larger_than_capacity() -> None:
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1, sleep=lambda _: None)
    with pytest.raises(ValueError, match="exceeds bucket capacity"):
        limiter.acquire("rdp-data", tokens=3)


def test_connection_pool_config_validates() -> None:
    config = ConnectionPoolConfig()
    assert config.max_keepalive_connections <= config.max_connections
    with pytest.raises(ValueError, match="max_keepalive_connections cannot exceed"):
        ConnectionPoolConfig(max_connections=2, max_keepalive_connections=5)
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        ConnectionPoolConfig(request_timeout_seconds=0)
