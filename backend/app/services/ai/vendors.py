"""The vendor vocabulary: who can be called, and when the tier moves on.

D-053 made the platform multi-vendor for ONE reason: losing credit on a vendor
must not stop AI features. Everything here exists to make the failover decision
structural rather than a guess, because the two failure families look identical
in a log line and must be handled in opposite ways.

* **Availability** — the vendor could not serve this request right now: quota or
  credit exhausted, the credential rejected, a 5xx, a timeout, a dead socket.
  The next vendor is tried.
* **Configuration** — the vendor is not usable in this deployment at all: no key,
  no model id, an endpoint that fails the egress guard, a model the vendor does
  not know, or no approved configuration for this environment. The vendor is
  SKIPPED with a named reason and the next one is tried. Never a silent
  fallthrough, and never a crash.
* **Content** — the vendor answered, and the answer is the outcome: a refusal, a
  truncated structured output, output that does not fit the schema, or (outside
  this module) a draft that fails grounding. The tier STOPS. Retrying an
  ungrounded draft on a second vendor would stampede all three providers with a
  request that is going to fail the same way, and it would spend a bank's quota
  three times to do it.

``bad_request`` is deliberately in none of the three sets: a 400 is our own
malformed request and would be a 400 everywhere, so it stops rather than
touring the vendors.

The decision is made on ``ModelResult.failure_code``, which is a closed
vocabulary produced from an exception CLASS or an HTTP status — never from a
message. Where a vendor's body is read at all (OpenAI's ``error.code``), it only
REFINES an already-availability status code; no failover decision depends on a
string a vendor could reword.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.config import (
    AI_EFFORT_UNSUPPORTED,
    AI_VENDOR_EFFORT_LEVELS,
    AI_VENDORS,
    AiVendor,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ai.client import ModelResult

#: The vendor could not serve the request NOW. Try the next one.
AVAILABILITY_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "rate_limited",
        "insufficient_quota",
        "overloaded",
        # 401/403. The vendor rejected the credential we hold, which for a
        # pre-paid account is indistinguishable from "this account is finished"
        # — and either way this vendor cannot serve the request.
        "not_configured",
        "upstream_error",
        "timeout",
        "connection",
    }
)

#: The vendor is not usable in this deployment. Skip it, by name.
CONFIGURATION_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "key_missing",
        "model_unconfigured",
        "endpoint_missing",
        "endpoint_invalid",
        "model_unavailable",
        "not_approved",
        # D-061, per-feature model selection: a pinned feature pointed at a
        # floating id, a floating id pointed at a vendor that publishes none, and
        # an override naming a feature that does not exist.
        "model_not_pinned",
        "model_alias_unsupported",
        "model_override_invalid",
    }
)

#: Outcomes that are ABOUT THE ANSWER. The tier never advances past one.
CONTENT_OUTCOMES: frozenset[str] = frozenset({"ok", "refused", "truncated", "schema_invalid"})

#: Capability names recorded when a vendor cannot honour part of the request.
#: Anthropic-only behaviour must degrade VISIBLY (D-053), so each of these lands
#: on the draft's provenance and in the filed artifact's evidence.
CAP_PROMPT_CACHE = "prompt_cache_control"
CAP_SERVER_SIDE_FALLBACKS = "server_side_fallbacks"
CAP_ADAPTIVE_THINKING = "adaptive_thinking"
CAP_EFFORT_LEVEL = "effort_level"

DEGRADABLE_CAPABILITIES: frozenset[str] = frozenset(
    {CAP_PROMPT_CACHE, CAP_SERVER_SIDE_FALLBACKS, CAP_ADAPTIVE_THINKING, CAP_EFFORT_LEVEL}
)


class VendorUnavailable(Exception):
    """This vendor cannot be prepared in this deployment.

    Raised by an adapter's ``prepare`` — before any socket is opened — and caught
    by the tier, which records ``code`` against the vendor and moves on. ``code``
    is always in :data:`CONFIGURATION_FAILURE_CODES`; it never carries a key, a
    URL or a vendor message.
    """

    def __init__(self, vendor: str, code: str) -> None:
        super().__init__(f"{vendor}: {code}")
        self.vendor = vendor
        self.code = code


@dataclass(frozen=True)
class VendorDescriptor:
    """What a prepared vendor WOULD send, known before it is called.

    The tier needs this to check the per-vendor approved configuration without
    constructing a client, and the provenance stamp needs it afterwards.
    """

    vendor: AiVendor
    #: The RESOLVED model id (D-061): a feature's override if it has one, else the
    #: vendor default. This is the id the approval key is looked up with and the
    #: id stamped into a filed document's evidence, so it is the one that will
    #: actually be sent — an override cannot route around either.
    model: str
    effort: str
    #: Request features this vendor cannot honour. Recorded, never dropped.
    degraded: tuple[str, ...] = ()
    #: Where ``model`` came from: ``feature_override`` or ``vendor_default``.
    model_source: str | None = None


def resolve_effort(vendor: str, configured: str) -> tuple[str, bool]:
    """The effort this vendor will actually use, and whether that is a downgrade.

    The vendor's native vocabulary lives in ``AI_VENDOR_EFFORT_LEVELS`` (config,
    per D-024) so an adapter never names a level itself. A vendor with no effort
    control at all resolves to :data:`~app.core.config.AI_EFFORT_UNSUPPORTED`.
    """
    native = AI_VENDOR_EFFORT_LEVELS.get(vendor, ())
    if not native:
        return AI_EFFORT_UNSUPPORTED, True
    if configured in native:
        return configured, False
    # Ascending vocabulary: the closest the vendor has to what was asked for.
    return native[-1], True


def advances(result: ModelResult[Any]) -> bool:
    """Should the tier try the next vendor after this result?"""
    if result.outcome in CONTENT_OUTCOMES:
        return False
    code = result.failure_code or ""
    return code in AVAILABILITY_FAILURE_CODES or code in CONFIGURATION_FAILURE_CODES


def failure_class(code: str | None) -> str:
    """``availability`` / ``configuration`` / ``terminal`` — for the audit line."""
    if code in AVAILABILITY_FAILURE_CODES:
        return "availability"
    if code in CONFIGURATION_FAILURE_CODES:
        return "configuration"
    return "terminal"


__all__ = [
    "AI_VENDORS",
    "AVAILABILITY_FAILURE_CODES",
    "CAP_ADAPTIVE_THINKING",
    "CAP_EFFORT_LEVEL",
    "CAP_PROMPT_CACHE",
    "CAP_SERVER_SIDE_FALLBACKS",
    "CONFIGURATION_FAILURE_CODES",
    "CONTENT_OUTCOMES",
    "DEGRADABLE_CAPABILITIES",
    "AiVendor",
    "VendorDescriptor",
    "VendorUnavailable",
    "advances",
    "failure_class",
    "resolve_effort",
]
