"""Bloomberg transport seam: one vendor request, one raw response dict.

The transport is the only place a live Bloomberg connection would exist.
MVP ships two implementations, neither of which touches the network:

- :class:`FixtureTransport` replays recorded responses keyed by scope
  (§6.5 — fixtures are recorded Bloomberg responses; recording is a one-time
  operation against a Bloomberg dev environment).
- :class:`UnavailableTransport` (the default when no transport is injected)
  classifies every request as VENDOR_UNAVAILABLE — the live B-PIPE / Data
  License transport is Phase 2 and plugs in behind :class:`BlpTransport`
  without touching extractors or translators.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.adapters.market_data.bloomberg.auth import VENDOR_DISPLAY_NAME
from app.adapters.market_data.errors import (
    BankFacingErrorCode,
    MarketDataError,
    render_bank_facing,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.adapters.market_data.bloomberg.auth import BloombergSession

# The transport seam pre-dates a real cache lookup; the notification layer
# owns rendering the actual last-successful-pull timestamp (§12.4).
_NO_CACHE_TIMESTAMP = "not available"


class BlpTransport(Protocol):
    """Executes one Bloomberg request against an authenticated session.

    ``request_spec`` is the normalized request built by an extractor from the
    field catalog (``{"scope": ..., "securities": [...], "fields": [...]}``);
    the return value is the raw vendor response as a dict. Vendor faults are
    raised as :class:`MarketDataError` — never as raw vendor exceptions.
    """

    def request(
        self, session: BloombergSession, request_spec: dict[str, Any]
    ) -> dict[str, Any]: ...


class UnavailableTransport:
    """Default transport: live Bloomberg connectivity is not configured."""

    def request(self, session: BloombergSession, request_spec: dict[str, Any]) -> dict[str, Any]:
        raise MarketDataError(
            render_bank_facing(
                BankFacingErrorCode.VENDOR_UNAVAILABLE,
                vendor=VENDOR_DISPLAY_NAME,
                timestamp=_NO_CACHE_TIMESTAMP,
            ),
            internal_detail="live Bloomberg transport not configured (Phase 2)",
        )


class FixtureTransport:
    """Replays recorded Bloomberg responses from a fixtures directory.

    Responses are keyed by the requesting scope: ``request_spec["scope"]`` is
    resolved through ``filenames`` (falling back to ``<SCOPE>.json``) inside
    ``fixtures_dir``. A missing recording classifies as VENDOR_UNAVAILABLE —
    the simulated vendor cannot serve what was never recorded.
    """

    def __init__(
        self,
        fixtures_dir: Path | str,
        filenames: Mapping[str, str] | None = None,
    ) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._filenames = dict(filenames or {})

    def request(self, session: BloombergSession, request_spec: dict[str, Any]) -> dict[str, Any]:
        scope_name = str(request_spec.get("scope", ""))
        path = self._fixtures_dir / self._filenames.get(scope_name, f"{scope_name}.json")
        if not path.is_file():
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.VENDOR_UNAVAILABLE,
                    vendor=VENDOR_DISPLAY_NAME,
                    timestamp=_NO_CACHE_TIMESTAMP,
                ),
                internal_detail=f"no recorded fixture for scope {scope_name!r} at {path}",
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise MarketDataError(
                render_bank_facing(
                    BankFacingErrorCode.VENDOR_UNAVAILABLE,
                    vendor=VENDOR_DISPLAY_NAME,
                    timestamp=_NO_CACHE_TIMESTAMP,
                ),
                internal_detail=f"fixture {path} is not a JSON object",
            )
        return payload
