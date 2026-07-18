"""Session-management resilience for the RDP transport (§7.3 hardening).

The live Refinitiv Data Platform transport (Phase 2) must retry transient
failures, respect RDP's per-endpoint request quotas, and pool HTTP
connections behind the OAuth session token. This module supplies those
primitives as deterministic, injectable seams (clock/sleep/jitter) so they
are exercised without any live vendor or real wall-clock:

- :class:`RetryPolicy` + :func:`retry_with_backoff` — bounded exponential
  backoff with full jitter, retrying ONLY transient classified failures
  (``VENDOR_UNAVAILABLE`` / ``RATE_LIMITED`` / ``NETWORK_ERROR``). RDP's
  ``429 Too Many Requests`` classifies as ``RATE_LIMITED`` and is retried;
  ``401``/``403`` (credential / scope) are terminal and surface at once.
- :class:`TokenBucketRateLimiter` — per-endpoint token bucket so the pull
  loop respects each RDP data endpoint's quota.
- :class:`ConnectionPoolConfig` — the ``httpx`` connection-pool limits the
  live transport applies to its keep-alive RDP client.

Kept intentionally parallel to the Bloomberg resilience module: the two
vendors meter and fail differently in detail, but the resilience contract the
pull loop programs against is identical.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError

# RDP transient conditions worth retrying. 429/5xx map to these codes in the
# transport's classification table; credential/scope codes are excluded.
DEFAULT_RETRYABLE_CODES: frozenset[BankFacingErrorCode] = frozenset(
    {
        BankFacingErrorCode.VENDOR_UNAVAILABLE,
        BankFacingErrorCode.RATE_LIMITED,
        BankFacingErrorCode.NETWORK_ERROR,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter for RDP calls.

    ``max_attempts`` counts the first try. The backoff ceiling doubles each
    attempt from ``base_delay_seconds`` up to ``max_delay_seconds``; full
    jitter samples the actual sleep uniformly in ``[0, ceiling]``.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    retryable_codes: frozenset[BankFacingErrorCode] = DEFAULT_RETRYABLE_CODES

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            msg = "delays must be non-negative"
            raise ValueError(msg)

    def backoff_ceiling(self, attempt: int) -> float:
        """The un-jittered backoff ceiling before ``attempt`` (1-indexed)."""
        if attempt < 1:
            msg = "attempt is 1-indexed"
            raise ValueError(msg)
        exponential = self.base_delay_seconds * (2 ** (attempt - 1))
        return min(exponential, self.max_delay_seconds)

    def is_retryable(self, error: MarketDataError) -> bool:
        return error.bank_facing.code in self.retryable_codes


def retry_with_backoff[T](
    operation: Callable[[], T],
    policy: RetryPolicy | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] | None = None,
) -> T:
    """Call ``operation`` with bounded retry on transient classified failures.

    Only :class:`MarketDataError` whose code is retryable is retried; every
    other exception propagates immediately. The final transient error is
    re-raised unchanged, preserving its bank-facing classification. ``sleep``
    and ``jitter`` are injectable for deterministic tests.
    """
    policy = policy or RetryPolicy()
    sample = jitter if jitter is not None else (lambda ceiling: random.uniform(0, ceiling))

    last_error: MarketDataError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except MarketDataError as exc:
            if not policy.is_retryable(exc) or attempt == policy.max_attempts:
                raise
            last_error = exc
            delay = sample(policy.backoff_ceiling(attempt))
            if delay > 0:
                sleep(delay)
    if last_error is not None:
        raise last_error
    msg = "retry_with_backoff exhausted without an outcome"
    raise RuntimeError(msg)


@dataclass
class TokenBucketRateLimiter:
    """Per-endpoint token bucket honoring each RDP endpoint's request quota.

    Each endpoint key gets an independent bucket of ``capacity`` tokens
    refilling at ``refill_per_second``. :meth:`acquire` blocks (via the
    injected ``sleep``) until the requested tokens are available, then debits
    them. ``clock`` and ``sleep`` are injectable so tests pass no real time.
    """

    capacity: float
    refill_per_second: float
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            msg = "capacity must be positive"
            raise ValueError(msg)
        if self.refill_per_second <= 0:
            msg = "refill_per_second must be positive"
            raise ValueError(msg)

    def _refill(self, endpoint: str) -> tuple[float, float]:
        now = self.clock()
        tokens, last = self._buckets.get(endpoint, (self.capacity, now))
        elapsed = max(0.0, now - last)
        tokens = min(self.capacity, tokens + elapsed * self.refill_per_second)
        return tokens, now

    def acquire(self, endpoint: str, tokens: float = 1.0) -> float:
        """Debit ``tokens`` from ``endpoint``'s bucket, sleeping if needed.

        Returns the seconds waited. A request larger than ``capacity`` can
        never be satisfied and raises ``ValueError``.
        """
        if tokens > self.capacity:
            msg = f"request of {tokens} tokens exceeds bucket capacity {self.capacity}"
            raise ValueError(msg)
        available, now = self._refill(endpoint)
        waited = 0.0
        if available < tokens:
            deficit = tokens - available
            wait = deficit / self.refill_per_second
            self.sleep(wait)
            waited = wait
            available = min(self.capacity, available + wait * self.refill_per_second)
            now = self.clock()
        self._buckets[endpoint] = (available - tokens, now)
        return waited


@dataclass(frozen=True)
class ConnectionPoolConfig:
    """``httpx`` connection-pool limits for the live RDP transport.

    RDP calls reuse a keep-alive HTTP/1.1 (or HTTP/2) client authenticated
    with the OAuth session token; these bounds cap concurrent and idle
    connections so a burst of scope pulls cannot exhaust local sockets.
    """

    max_connections: int = 10
    max_keepalive_connections: int = 5
    keepalive_expiry_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_connections < 1:
            msg = "max_connections must be at least 1"
            raise ValueError(msg)
        if self.max_keepalive_connections < 0:
            msg = "max_keepalive_connections must be non-negative"
            raise ValueError(msg)
        if self.max_keepalive_connections > self.max_connections:
            msg = "max_keepalive_connections cannot exceed max_connections"
            raise ValueError(msg)
        for name in (
            "keepalive_expiry_seconds",
            "connect_timeout_seconds",
            "request_timeout_seconds",
        ):
            if getattr(self, name) <= 0:
                msg = f"{name} must be positive"
                raise ValueError(msg)
