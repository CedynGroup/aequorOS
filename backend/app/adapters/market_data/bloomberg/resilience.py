"""Session-management resilience for the Bloomberg transport (§6.3 hardening).

The MVP ships fixture and unavailable transports (no network), but the live
B-PIPE / Data License transport (Phase 2) must be resilient the day it is
wired. This module provides the three primitives the spec's operational
requirements call for, all deterministic and unit-testable via injected
clock/sleep/rng seams so they need no live vendor and no real wall-clock:

- :class:`RetryPolicy` + :func:`retry_with_backoff` — bounded retry with
  exponential backoff and full jitter, retrying ONLY transient classified
  failures (``VENDOR_UNAVAILABLE`` / ``RATE_LIMITED`` / ``NETWORK_ERROR``).
  Credential, scope, and unknown-instrument failures never retry — retrying
  a permission denial only wastes quota and delays the bank-facing message.
- :class:`TokenBucketRateLimiter` — per-endpoint token bucket so pulls
  respect each Bloomberg service's request quota (§11.2 references
  per-endpoint quotas); a live pull loop acquires before every request.
- :class:`ConnectionPoolConfig` — the connection-pool parameters the live
  session provider uses to reuse authenticated sessions across a pull cycle.

Nothing here touches the network; the live transport composes these around
its real vendor calls.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.adapters.market_data.errors import BankFacingErrorCode, MarketDataError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Classified failures worth retrying: transient vendor/network conditions.
# Everything else (credential/scope/quota/unknown-instrument) is terminal for
# the attempt and must surface immediately.
DEFAULT_RETRYABLE_CODES: frozenset[BankFacingErrorCode] = frozenset(
    {
        BankFacingErrorCode.VENDOR_UNAVAILABLE,
        BankFacingErrorCode.RATE_LIMITED,
        BankFacingErrorCode.NETWORK_ERROR,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter.

    ``base_delay_seconds * 2**(attempt-1)`` capped at ``max_delay_seconds``
    gives the backoff ceiling; full jitter samples the actual sleep uniformly
    in ``[0, ceiling]`` (AWS's recommended anti-thundering-herd strategy).
    ``max_attempts`` counts the first try, so ``3`` means one call plus two
    retries.
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

    Retries only :class:`MarketDataError` whose code is in
    ``policy.retryable_codes``; any other exception (including terminal
    classified errors) propagates on the first occurrence. After the final
    attempt the last transient error is re-raised unchanged so the bank-facing
    classification is preserved. ``sleep`` and ``jitter`` are injectable for
    deterministic tests (``jitter`` defaults to full jitter via
    :func:`random.uniform`).
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
    # Unreachable: the loop either returns, re-raises on the final attempt, or
    # re-raises a non-retryable error. Guard keeps the type checker honest.
    if last_error is not None:
        raise last_error
    msg = "retry_with_backoff exhausted without an outcome"
    raise RuntimeError(msg)


@dataclass
class TokenBucketRateLimiter:
    """Per-endpoint token bucket honoring each Bloomberg service's quota.

    Each distinct endpoint key gets an independent bucket of ``capacity``
    tokens refilling at ``refill_per_second``. :meth:`acquire` blocks (via the
    injected ``sleep``) only long enough for the requested tokens to be
    available, then debits them. ``clock`` and ``sleep`` are injectable so the
    limiter is fully deterministic under test — no real time passes.
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

        Returns the seconds waited (0 when tokens were immediately
        available). A single request larger than ``capacity`` is impossible to
        satisfy and raises ``ValueError`` rather than sleeping forever.
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
    """Connection-pool parameters for the live Bloomberg session provider.

    Bloomberg enterprise APIs authenticate a session that is reused across the
    requests of one pull cycle; the pool bounds concurrent sessions and evicts
    idle ones. These are declared here so the Phase 2 live provider consumes a
    single validated config object rather than scattered constants.
    """

    max_sessions: int = 4
    max_idle_seconds: float = 300.0
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_sessions < 1:
            msg = "max_sessions must be at least 1"
            raise ValueError(msg)
        for name in ("max_idle_seconds", "connect_timeout_seconds", "request_timeout_seconds"):
            if getattr(self, name) <= 0:
                msg = f"{name} must be positive"
                raise ValueError(msg)


def endpoints_of(request_specs: Iterable[Mapping[str, object]], default: str) -> set[str]:
    """The distinct Bloomberg data-source endpoints a set of specs will hit.

    Used by the live pull loop to rate-limit per endpoint: catalog entries
    carry a ``data_source`` (e.g. ``BVAL``); specs without one fall back to
    ``default`` (the reference-data service).
    """
    return {str(spec.get("data_source", default)) for spec in request_specs}
