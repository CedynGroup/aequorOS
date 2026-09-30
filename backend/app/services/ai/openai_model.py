"""The OpenAI adapter: tier position 2 in the default order (D-053).

Written against the REST Responses API over ``httpx``, not the ``openai`` SDK,
which is deliberately NOT a dependency of this service: the platform already
locks one model SDK (``anthropic``) for the vendor whose full request surface it
uses, and this adapter needs one POST with a JSON-schema response format. Adding
a second SDK to the lockfile to send that would be a heavier change than the
feature it serves, and it would need its own lazy-import guard besides. ``httpx``
is already a runtime dependency (via ``fastapi[standard]``) and is already the
platform's outbound HTTP client (the ORASS channel, the market desk, OpenBao).

The API shape below was written from the vendor's published Responses API
contract at implementation time (2026-09-22) and has NOT been exercised against
a live account from this codebase. That is exactly what
``approved_configurations.json`` is for: there is no entry for this vendor, so a
DEPLOYED environment skips this tier until one is reviewed in, and reviewing one
in means having run it. Local and test run it against a stub transport.

    POST {_API_BASE}/responses
    {
      "model": ..., "instructions": ..., "input": [{"role": "user", ...}],
      "max_output_tokens": ..., "reasoning": {"effort": ...},
      "text": {"format": {"type": "json_schema", "name": ..., "schema": ...,
                          "strict": true}}
    }

Three request features of the Anthropic path do NOT exist here and are RECORDED
as degraded rather than dropped quietly (``vendors.CAP_*``): per-block
``cache_control`` (this vendor caches a long prefix automatically and takes no
instruction about it), the server-side ``fallbacks`` beta, and adaptive thinking.
Reasoning effort exists but with a shorter vocabulary, so a configured ``xhigh``
or ``max`` resolves down and that, too, is recorded.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from app.core.config import SETTINGS_CONFIG, AiVendor, Settings, get_settings
from app.services.ai.client import ModelRequest, ModelResult, UsageRecord
from app.services.ai.features import AiFeature
from app.services.ai.vendors import (
    CAP_ADAPTIVE_THINKING,
    CAP_EFFORT_LEVEL,
    CAP_PROMPT_CACHE,
    CAP_SERVER_SIDE_FALLBACKS,
    VendorDescriptor,
    VendorUnavailable,
    resolve_effort,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    import httpx
    from pydantic import BaseModel

    from app.services.ai.client import DraftModel

VENDOR: AiVendor = "openai"

#: The vendor's published endpoint. An API contract, not a tunable — and not an
#: operator-supplied URL, so it is not an egress surface the way
#: ``GEMINI_API_URL`` is.
_API_BASE = "https://api.openai.com/v1"
_RESPONSES_PATH = "/responses"

#: The structured-output container this vendor uses, and the name it requires
#: alongside the schema.
_FORMAT_TYPE = "json_schema"
_SCHEMA_NAME = "structured_draft"

#: ``error.code`` for "this account has no credit left". A machine code, not a
#: message: it only REFINES a 429 that already fails over, so no failover
#: decision depends on wording the vendor could change.
_INSUFFICIENT_QUOTA = "insufficient_quota"

#: Stop signals. ``incomplete`` + this reason is the truncation case; a refusal
#: arrives as a content part of its own type.
_STATUS_INCOMPLETE = "incomplete"
_TRUNCATED_REASON = "max_output_tokens"
_REFUSAL_PART = "refusal"
_TEXT_PART = "output_text"
_MESSAGE_ITEM = "message"


class OpenAiCredentialSettings(BaseSettings):
    """The OpenAI key, read nowhere else.

    The same rule as ``client.AiCredentialSettings`` and for the same reason:
    instantiated only when this vendor is actually prepared, so no process that
    does not call it parses the key, and the key is passed EXPLICITLY on the
    request rather than left to any client's environment fallback.
    """

    model_config = SETTINGS_CONFIG

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def describe(settings: Settings, feature: AiFeature | None = None) -> VendorDescriptor:
    """What this adapter would send. Reads no credential.

    ``feature`` selects the model (D-061): its override if it has one, else the
    vendor default.
    """
    from app.services.ai import model_selection  # noqa: PLC0415 - avoids an import cycle

    resolved = model_selection.resolve(feature, VENDOR, settings)
    effort, downgraded = resolve_effort(VENDOR, settings.ai.effort)
    degraded = [CAP_PROMPT_CACHE, CAP_ADAPTIVE_THINKING]
    if settings.ai.fallbacks == "default":
        degraded.append(CAP_SERVER_SIDE_FALLBACKS)
    if downgraded:
        degraded.append(CAP_EFFORT_LEVEL)
    return VendorDescriptor(
        vendor=VENDOR,
        model=resolved.model,
        effort=effort,
        degraded=tuple(sorted(degraded)),
        model_source=resolved.source,
    )


def prepare(
    settings: Settings, feature: AiFeature | None = None
) -> tuple[VendorDescriptor, Callable[[], DraftModel]]:
    descriptor = describe(settings, feature)
    credentials = OpenAiCredentialSettings()
    if credentials.openai_api_key is None:
        raise VendorUnavailable(VENDOR, "key_missing")
    return descriptor, lambda: OpenAiModel(settings, feature=feature)


class OpenAiModel:
    """The real client. Constructed only on the AI worker."""

    VENDOR: AiVendor = VENDOR

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        feature: AiFeature | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = settings or get_settings()
        credentials = OpenAiCredentialSettings()
        if credentials.openai_api_key is None:
            raise VendorUnavailable(VENDOR, "key_missing")
        self._settings = settings
        self._feature: AiFeature | None = feature
        self._key = credentials.openai_api_key
        #: Test seam (``httpx.MockTransport``). No test reaches the network.
        self._transport = transport

    @property
    def descriptor(self) -> VendorDescriptor:
        return describe(self._settings, self._feature)

    # -- request ------------------------------------------------------------

    def _payload(self, request: ModelRequest[Any]) -> dict[str, Any]:
        descriptor = self.descriptor
        ai = self._settings.ai
        schema = request.output_type.model_json_schema()
        return {
            "model": descriptor.model,
            # The system blocks concatenated in order. There is no per-block
            # cache instruction to carry, which is why CAP_PROMPT_CACHE is
            # recorded as degraded instead of silently omitted.
            "instructions": "\n\n".join(block.text for block in request.system),
            "input": [{"role": "user", "content": request.user_content}],
            "max_output_tokens": ai.max_output_tokens,
            "reasoning": {"effort": descriptor.effort},
            "text": {
                "format": {
                    "type": _FORMAT_TYPE,
                    "name": _SCHEMA_NAME,
                    "schema": schema,
                    "strict": True,
                }
            },
        }

    def _client(self) -> httpx.Client:
        import httpx  # noqa: PLC0415 - the one lazy transport import in this adapter

        ai = self._settings.ai
        return httpx.Client(
            base_url=_API_BASE,
            headers={
                # Explicit, never the client's environment fallback: the same
                # reason ``client.py`` passes ``api_key=`` by hand.
                "Authorization": f"Bearer {self._key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=ai.request_timeout_seconds,
            transport=self._transport or httpx.HTTPTransport(retries=ai.max_retries),
            # A redirect from an API host is a second, unvalidated destination.
            follow_redirects=False,
        )

    def generate[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        import httpx  # noqa: PLC0415 - the one lazy transport import in this adapter

        started = time.monotonic()
        try:
            with self._client() as client:
                response = client.post(_RESPONSES_PATH, json=self._payload(request))
        except httpx.TimeoutException:
            return self._failure("timeout", started)
        except httpx.TransportError:
            # Every non-timeout transport fault: DNS, TLS, a reset socket.
            return self._failure("connection", started)
        if response.status_code != HTTPStatus.OK:
            return self._from_status(response, started)
        return self._from_response(request, response, started)

    # -- response -----------------------------------------------------------

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _common(self, started: float) -> dict[str, Any]:
        descriptor = self.descriptor
        return {
            "model_requested": descriptor.model,
            "vendor": descriptor.vendor,
            "degraded": descriptor.degraded,
            "model_source": descriptor.model_source,
            "latency_ms": self._elapsed_ms(started),
        }

    def _failure(
        self, code: str, started: float, *, request_id: str | None = None
    ) -> ModelResult[Any]:
        outcome = "rate_limited" if code in {"rate_limited", _INSUFFICIENT_QUOTA} else "failed"
        return ModelResult(
            outcome=outcome, failure_code=code, request_id=request_id, **self._common(started)
        )

    def _from_status(self, response: httpx.Response, started: float) -> ModelResult[Any]:
        """HTTP status -> outcome. The status decides; a body only refines."""
        # The raw int, never ``HTTPStatus(...)`` (audit A7-05): that constructor
        # raises ValueError on any code outside the enum, and 520/521/522/524/
        # 526/529 (Cloudflare) and 499 (nginx) are exactly the codes an
        # overloaded vendor edge returns. The exception escaped ``generate``, so
        # the single most obvious availability failure produced no failover and
        # stranded the job row. ``HTTPStatus`` is an ``IntEnum``, so comparing
        # against its members is still exact.
        status = response.status_code
        request_id = response.headers.get("x-request-id")
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            return self._failure(self._quota_code(response), started, request_id=request_id)
        if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            return self._failure("not_configured", started, request_id=request_id)
        if status == HTTPStatus.NOT_FOUND:
            return self._failure("model_unavailable", started, request_id=request_id)
        # ``>=`` and no upper bound: an HTTP status is three digits, so there is
        # nothing above 599 to exclude, and naming the floor through the enum
        # keeps this free of the numeric literals D-024 forbids in AI code.
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return self._failure("upstream_error", started, request_id=request_id)
        # Everything else, 400 included: our own malformed request. It would be
        # malformed at the next vendor too, so it must not fail over.
        return self._failure("bad_request", started, request_id=request_id)

    def _quota_code(self, response: httpx.Response) -> str:
        """``rate_limited`` or the more specific ``insufficient_quota``.

        Both are availability failures and both fail over, so this only sharpens
        what the audit row says happened.
        """
        try:
            body = response.json()
        except ValueError:
            return "rate_limited"
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return _INSUFFICIENT_QUOTA if code == _INSUFFICIENT_QUOTA else "rate_limited"

    def _from_response(  # noqa: PLR0911 - one return per response shape reads better
        self, request: ModelRequest[Any], response: httpx.Response, started: float
    ) -> ModelResult[Any]:
        try:
            body = response.json()
        except ValueError:
            return ModelResult(
                outcome="schema_invalid", failure_code="parse_failed", **self._common(started)
            )
        common: dict[str, Any] = {
            **self._common(started),
            "model_served": body.get("model"),
            "request_id": body.get("id") or response.headers.get("x-request-id"),
            "usage": _usage(body.get("usage")),
        }
        parts = _content_parts(body)

        # Refusal FIRST, and the refusal part's text is never read — the same
        # rule as the Anthropic path: a refusal is a status, not prose.
        refusal = next((part for part in parts if part.get("type") == _REFUSAL_PART), None)
        if refusal is not None:
            return ModelResult(outcome="refused", stop_reason=_REFUSAL_PART, **common)
        if body.get("status") == _STATUS_INCOMPLETE:
            reason = (body.get("incomplete_details") or {}).get("reason")
            if reason == _TRUNCATED_REASON:
                return ModelResult(
                    outcome="truncated",
                    failure_code=_TRUNCATED_REASON,
                    stop_reason=reason,
                    **common,
                )
            return ModelResult(
                outcome="schema_invalid", failure_code="incomplete", stop_reason=reason, **common
            )

        text = "".join(
            str(part.get("text") or "") for part in parts if part.get("type") == _TEXT_PART
        )
        if not text:
            return ModelResult(outcome="schema_invalid", failure_code="no_parsed_output", **common)
        try:
            parsed = request.output_type.model_validate_json(text)
        except Exception:  # noqa: BLE001 - a validation failure is an outcome
            return ModelResult(outcome="schema_invalid", failure_code="parse_failed", **common)
        return ModelResult(outcome="ok", parsed=parsed, stop_reason=body.get("status"), **common)


def _content_parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Every content part of the assistant message, reasoning items skipped."""
    parts: list[dict[str, Any]] = []
    for item in body.get("output") or ():
        if not isinstance(item, dict) or item.get("type") != _MESSAGE_ITEM:
            continue
        for part in item.get("content") or ():
            if isinstance(part, dict):
                parts.append(part)
    return parts


def _usage(raw: Any) -> UsageRecord | None:
    """This vendor's token counts, mapped onto the shared record.

    ``cached_tokens`` is the vendor's automatic prefix cache. It is reported as
    ``cache_read_input_tokens`` because that is what the number means, but the
    platform never ASKED for it — which is why the request still records
    ``prompt_cache_control`` as a degraded capability.
    """
    if not isinstance(raw, dict):
        return None
    details = raw.get("input_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    return UsageRecord(
        input_tokens=raw.get("input_tokens"),
        output_tokens=raw.get("output_tokens"),
        cache_read_input_tokens=cached,
    )


__all__ = [
    "VENDOR",
    "OpenAiCredentialSettings",
    "OpenAiModel",
    "describe",
    "prepare",
]
