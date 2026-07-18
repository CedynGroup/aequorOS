"""RDP transport seam: how the Refinitiv adapter reaches (or simulates) LSEG.

The adapter never talks HTTP directly; it hands an opaque ``request_spec``
to an :class:`RdpTransport` and receives raw RDP-shaped JSON back. MVP ships
two implementations:

- :class:`FixtureTransport` replays recorded RDP responses keyed by scope
  (§7.4 / §16.3): development, CI, and contract tests run without any live
  RDP dependency.
- :class:`UnconfiguredTransport` is the default when no transport is
  injected: every fetch fails with a classified ``VENDOR_UNAVAILABLE`` —
  live RDP wiring is Phase 2 (§14.2) and slots in as a new ``RdpTransport``
  implementation without touching the adapter, extractors, or translators.

Error classification also lives here (§12): raw vendor error payloads are
mapped to :class:`BankFacingErrorCode` values and travel only inside
``MarketDataError.internal_detail``. Raw vendor text never surfaces.

Fixture-recorded response shape (all supported scopes)::

    {
      "universe": ["GH1M=", ...],          # RICs requested
      "fields": ["TR.MidYield", ...],      # fields requested
      "data": [[ric, field, value, ...]],  # one row per (ric, field)
      "vendor_internal": {...},            # vendor debug block, never surfaced
      "error": {"code": ..., "http_status": ..., "message": ...}  # on failure
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.adapters.market_data.errors import (
    BankFacingError,
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)
from app.adapters.market_data.refinitiv.resilience import (
    ConnectionPoolConfig,
    RetryPolicy,
    TokenBucketRateLimiter,
)
from app.adapters.market_data.scope_taxonomy import DataScope

VENDOR_NAME = "refinitiv"
VENDOR_LABEL = "Refinitiv (LSEG)"

# Single RDP data-service endpoint key for per-endpoint rate limiting. RDP
# meters the data-access API (``/data/...``) distinctly from the token
# endpoint; the token endpoint is rate-limited inside the auth layer.
_DATA_ENDPOINT = "rdp-data"

# Bank-facing templates reference the last successful pull; the adapter layer
# has no cache lookup in scope here, so the placeholder stays honest.
_LAST_PULL_TIMESTAMP = "unknown"
_DEFAULT_SCOPE_LABEL = "the requested scope"

# Normalized vendor error codes as they appear in recorded RDP fixtures,
# mapped to bank-facing classifications (§12.1). Unrecognized codes fall back
# to VENDOR_UNAVAILABLE: an unclassifiable vendor failure is treated as an
# outage, never surfaced raw.
_ERROR_CODE_CLASSIFICATION: dict[str, BankFacingErrorCode] = {
    "invalid_client": BankFacingErrorCode.CREDENTIAL_INVALID,
    "invalid_grant": BankFacingErrorCode.CREDENTIAL_EXPIRED,
    "token_expired": BankFacingErrorCode.CREDENTIAL_EXPIRED,
    "access_revoked": BankFacingErrorCode.CREDENTIAL_REVOKED,
    "access_denied": BankFacingErrorCode.SCOPE_NOT_PERMITTED,
    "insufficient_scope": BankFacingErrorCode.SCOPE_NOT_PERMITTED,
    "instrument_not_found": BankFacingErrorCode.UNKNOWN_INSTRUMENT,
    "too_many_requests": BankFacingErrorCode.RATE_LIMITED,
    "server_error": BankFacingErrorCode.VENDOR_UNAVAILABLE,
    "service_unavailable": BankFacingErrorCode.VENDOR_UNAVAILABLE,
}
_HTTP_STATUS_CLASSIFICATION: dict[int, BankFacingErrorCode] = {
    401: BankFacingErrorCode.CREDENTIAL_INVALID,
    403: BankFacingErrorCode.SCOPE_NOT_PERMITTED,
    404: BankFacingErrorCode.UNKNOWN_INSTRUMENT,
    429: BankFacingErrorCode.RATE_LIMITED,
}

# Recorded fixture filenames per supported scope (§7.4). Tests may override
# per-scope filenames (e.g. to replay an error response) via the
# ``filenames`` constructor argument.
DEFAULT_FIXTURE_FILENAMES: dict[str, str] = {
    DataScope.YIELD_CURVE_GHS.value: "ghs_yield_curve.json",
    DataScope.FX_SPOT_USD_GHS.value: "usd_ghs_spot.json",
    DataScope.CREDIT_RATING_GHANA_SOVEREIGN.value: "ghana_sovereign_ratings.json",
}


def bank_facing_for(code: BankFacingErrorCode, scope_label: str | None = None) -> BankFacingError:
    """Render the pre-authored §12 template for ``code`` with Refinitiv params.

    All business-level placeholders are supplied; ``str.format`` ignores the
    ones a given template does not use.
    """
    return render_bank_facing(
        code,
        vendor=VENDOR_LABEL,
        timestamp=_LAST_PULL_TIMESTAMP,
        scope=scope_label or _DEFAULT_SCOPE_LABEL,
    )


def raise_for_vendor_error(payload: dict[str, Any], scope_label: str) -> None:
    """Classify and raise if an RDP payload carries a vendor error block.

    The raw error block (message, internal codes, debug fields) goes into
    ``internal_detail`` only; the bank sees the classified template.
    """
    error = payload.get("error")
    if not isinstance(error, dict):
        return
    code = _ERROR_CODE_CLASSIFICATION.get(str(error.get("code", "")))
    if code is None:
        http_status = error.get("http_status")
        code = (
            _HTTP_STATUS_CLASSIFICATION.get(http_status) if isinstance(http_status, int) else None
        )
    if code is None:
        code = BankFacingErrorCode.VENDOR_UNAVAILABLE
    raise MarketDataError(
        bank_facing_for(code, scope_label),
        internal_detail=(
            f"raw RDP error for {scope_label}: {json.dumps(error, sort_keys=True, default=str)}"
        ),
    )


def data_rows(payload: dict[str, Any], scope_label: str) -> list[list[Any]]:
    """The ``data`` rows of an RDP-shaped payload, shape-validated.

    Each row is ``[ric, field, value, ...]``; category-specific extractors
    interpret any extra columns. A malformed payload is a vendor problem and
    classifies as VENDOR_UNAVAILABLE with the specifics kept internal.
    """
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise MarketDataError(
            bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_label),
            internal_detail=f"RDP response for {scope_label} lacks a 'data' row list",
        )
    validated: list[list[Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 3:
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_label),
                internal_detail=(
                    f"RDP response for {scope_label} has malformed data row {index}: {row!r}"
                ),
            )
        validated.append(row)
    return validated


def request_spec_for(scope: DataScope, request_specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the opaque transport request for one scope's catalog specs."""
    universe: list[str] = []
    fields: list[str] = []
    for spec in request_specs:
        ric = str(spec.get("ric", ""))
        field = str(spec.get("field", ""))
        if ric and ric not in universe:
            universe.append(ric)
        if field and field not in fields:
            fields.append(field)
    return {"scope": scope.value, "universe": universe, "fields": fields}


class RdpTransport(Protocol):
    """The seam between the Refinitiv adapter and the RDP wire (or fixtures)."""

    def fetch(self, session_token: str, request_spec: dict[str, Any]) -> dict[str, Any]:
        """Return the raw RDP-shaped JSON response for one scope request."""
        ...


class UnconfiguredTransport:
    """Default transport: no live RDP wiring exists in MVP (§14.1).

    Every fetch fails as a classified vendor-unavailable error so nothing
    upstream ever mistakes a missing Phase 2 integration for vendor data.
    """

    def fetch(self, session_token: str, request_spec: dict[str, Any]) -> dict[str, Any]:
        scope_label = str(request_spec.get("scope", "")) or _DEFAULT_SCOPE_LABEL
        raise MarketDataError(
            bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_label),
            internal_detail="live RDP transport not configured (Phase 2)",
        )


class LiveRdpTransport:
    """Phase 2 seam for the live RDP data transport (§7.3 / §14.2).

    Composes the session-management hardening the live loop needs: a
    :class:`TokenBucketRateLimiter` acquired before every request (per-endpoint
    quota), a :class:`RetryPolicy` for :func:`retry_with_backoff` around the
    HTTP call, and a :class:`ConnectionPoolConfig` for the keep-alive ``httpx``
    client. Until real credentials are wired (questions/Q03), ``fetch`` acquires
    the rate-limit token (proving the wiring) and then classifies as
    ``VENDOR_UNAVAILABLE`` — it never touches the network.
    """

    def __init__(
        self,
        *,
        rate_limiter: TokenBucketRateLimiter | None = None,
        retry_policy: RetryPolicy | None = None,
        pool_config: ConnectionPoolConfig | None = None,
    ) -> None:
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter(
            capacity=20, refill_per_second=10
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self.pool_config = pool_config or ConnectionPoolConfig()

    def fetch(self, session_token: str, request_spec: dict[str, Any]) -> dict[str, Any]:
        self.rate_limiter.acquire(_DATA_ENDPOINT)
        _ = session_token  # the live client authenticates with this per request
        scope_label = str(request_spec.get("scope", "")) or _DEFAULT_SCOPE_LABEL
        raise MarketDataError(
            bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_label),
            internal_detail=(
                "live RDP transport is fenced (Phase 2); request for scope "
                f"{scope_label!r} not dispatched"
            ),
        )


class FixtureTransport:
    """Replays recorded RDP responses from JSON fixture files keyed by scope.

    ``filenames`` overrides (or extends) :data:`DEFAULT_FIXTURE_FILENAMES`
    per scope value; scopes without an explicit mapping fall back to
    ``<scope_value_lowercase>.json``. A missing fixture classifies as
    VENDOR_UNAVAILABLE — exactly what an unreachable vendor would produce.
    """

    def __init__(
        self,
        fixtures_dir: Path | str,
        filenames: dict[str, str] | None = None,
    ) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._filenames = {**DEFAULT_FIXTURE_FILENAMES, **(filenames or {})}

    def fetch(self, session_token: str, request_spec: dict[str, Any]) -> dict[str, Any]:
        scope_value = str(request_spec.get("scope", ""))
        filename = self._filenames.get(scope_value, f"{scope_value.lower()}.json")
        path = self._fixtures_dir / filename
        if not path.is_file():
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_value),
                internal_detail=f"no recorded RDP fixture for scope {scope_value!r} at {path}",
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise MarketDataError(
                bank_facing_for(BankFacingErrorCode.VENDOR_UNAVAILABLE, scope_value),
                internal_detail=f"RDP fixture {path} is not a JSON object",
            )
        return payload
