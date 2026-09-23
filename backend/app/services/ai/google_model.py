"""The Google Gemini adapter: tier position 3 in the default order (D-053).

Over ``httpx`` rather than ``google-genai`` for the reason set out in
``openai_model``: the platform locks one model SDK, and this adapter needs one
POST. The wire shape was written from the published ``generateContent`` contract
at implementation time (2026-09-22) and has NOT been exercised against a live
project from this codebase — which is why there is no ``approved_configurations``
entry for it, and therefore why a DEPLOYED environment skips this tier until one
is reviewed in.

    POST {endpoint}            (endpoint from GEMINI_API_URL, see below)
    x-goog-api-key: <key>
    {
      "systemInstruction": {"parts": [{"text": ...}]},
      "contents": [{"role": "user", "parts": [{"text": ...}]}],
      "generationConfig": {"responseMimeType": "application/json",
                           "responseJsonSchema": {...},
                           "maxOutputTokens": ...}
    }

**``GEMINI_API_URL`` is an operator-supplied OUTBOUND URL, so it is an SSRF
surface and it is validated, never handed to a client verbatim.** It goes through
:func:`app.core.outbound.check_url` — the same authoritative, resolving guard the
Database-Direct, Temenos and ORASS connectors use — which allows ``https`` only
and refuses a loopback, RFC1918, link-local or cloud-metadata destination, and
refuses a name that resolves to one. A URL that does not pass is
``endpoint_invalid``: this tier is skipped by name, and nothing is sent. Every
redirect is re-checked by the same guard, because a permitted host can still
answer ``302 Location: http://169.254.169.254/``.

The endpoint is used as the operator wrote it, except for the model: if the path
names one (``/v1beta/models/<id>:generateContent``) it is replaced by the id
``model_selection`` resolves — the feature's override, else ``AI_GOOGLE_MODEL`` —
so the model id an approved configuration pins is the model id that is actually
called. Only when NEITHER names one does the URL's own id stand in. With no model
anywhere the tier is skipped with ``model_unconfigured`` rather than guessing one.

Anthropic-only behaviour that does NOT transfer and is recorded as degraded
(``vendors.CAP_*``): per-block ``cache_control``, the server-side ``fallbacks``
beta, adaptive thinking, and effort control — this vendor's thinking controls are
model-generation specific, so rather than send a field that may 400 on the
operator's chosen model, none is sent and the omission is on the record.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from app.core.config import SETTINGS_CONFIG, AiVendor, Settings, get_settings
from app.core.outbound import OutboundTargetBlocked, check_url, redirect_guard
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

VENDOR: AiVendor = "google"

#: The field name in the egress guard's audit line and refusal reason.
_URL_FIELD = "GEMINI_API_URL"

#: Path grammar of the generate endpoint. API contract, not tunable.
_MODELS_SEGMENT = "models"
_GENERATE_METHOD = "generateContent"

#: Structured output. ``responseJsonSchema`` takes standard JSON Schema
#: (``$defs``/``$ref`` included), which is what pydantic emits. The older
#: ``responseSchema`` is an OpenAPI subset that needs upper-case type names and
#: an inlined schema — if a deployment's model only supports that one, this is
#: the single line to change, and its approved configuration is what proves it.
_SCHEMA_FIELD = "responseJsonSchema"
_JSON_MIME = "application/json"

#: Finish reasons. Anything that is not a clean stop or a truncation is a refusal
#: — this vendor reports safety blocks as a finish reason rather than an outcome
#: of its own, and its text is never read either way.
_FINISH_OK = "STOP"
_FINISH_TRUNCATED = "MAX_TOKENS"


class GoogleEndpointSettings(BaseSettings):
    """The endpoint URL. NOT a secret, and deliberately separate from the key.

    Split from the credential class so ``describe`` can answer "what would this
    vendor send" in the API process — which the enqueue gate needs — without that
    process parsing a model key.
    """

    model_config = SETTINGS_CONFIG

    gemini_api_url: str | None = Field(default=None, alias="GEMINI_API_URL")

    @field_validator("gemini_api_url", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class GoogleCredentialSettings(BaseSettings):
    """The Gemini key, read nowhere else.

    Same rule as ``client.AiCredentialSettings``: instantiated only when this
    vendor is prepared, and the key is sent EXPLICITLY on the request header
    rather than left to any client's environment fallback.
    """

    model_config = SETTINGS_CONFIG

    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def _model_in_path(path: str) -> str | None:
    """The model id an endpoint path names, if it names one."""
    segments = [segment for segment in path.split("/") if segment]
    if _MODELS_SEGMENT not in segments:
        return None
    tail = segments[segments.index(_MODELS_SEGMENT) + 1 :]
    if not tail:
        return None
    return tail[0].split(":")[0] or None


def _endpoint(raw: str, model: str) -> str:
    """The URL to POST: the operator's endpoint with ``model`` pinned into it."""
    parts = urlsplit(raw.strip())
    segments = [segment for segment in parts.path.split("/") if segment]
    if _MODELS_SEGMENT in segments:
        segments = segments[: segments.index(_MODELS_SEGMENT)]
    path = "/" + "/".join([*segments, _MODELS_SEGMENT, f"{model}:{_GENERATE_METHOD}"])
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _resolved(settings: Settings, feature: AiFeature | None) -> tuple[str, str, str]:
    """``(endpoint, model, model_source)``, or a named skip. Reads no credential.

    The model comes from ``model_selection`` (D-061) — a feature's override if it
    has one, else ``AI_GOOGLE_MODEL`` — and only if NEITHER names one does the id
    in ``GEMINI_API_URL`` stand in, because an approval pins the id the request
    actually carries and the URL is the endpoint, not the choice.
    """
    from app.services.ai import model_selection  # noqa: PLC0415 - avoids an import cycle

    raw = GoogleEndpointSettings().gemini_api_url
    if not raw:
        raise VendorUnavailable(VENDOR, "endpoint_missing")
    try:
        resolved = model_selection.resolve(feature, VENDOR, settings)
    except VendorUnavailable as unresolved:
        if unresolved.code != "model_unconfigured":
            raise
        in_path = _model_in_path(urlsplit(raw).path)
        if not in_path:
            raise
        return _endpoint(raw, in_path), in_path, "vendor_default"
    return _endpoint(raw, resolved.model), resolved.model, resolved.source


def describe(settings: Settings, feature: AiFeature | None = None) -> VendorDescriptor:
    """What this adapter would send. Reads no credential, resolves no DNS.

    The URL's SYNTAX is not screened here: the authoritative, resolving check
    belongs immediately before the socket opens (``prepare``), and a describe that
    resolved names would make the enqueue gate do DNS on every request.
    """
    _endpoint_url, model, model_source = _resolved(settings, feature)
    effort, _downgraded = resolve_effort(VENDOR, settings.ai.effort)
    degraded = [CAP_PROMPT_CACHE, CAP_ADAPTIVE_THINKING, CAP_EFFORT_LEVEL]
    if settings.ai.fallbacks == "default":
        degraded.append(CAP_SERVER_SIDE_FALLBACKS)
    return VendorDescriptor(
        vendor=VENDOR,
        model=model,
        effort=effort,
        degraded=tuple(sorted(degraded)),
        model_source=model_source,
    )


def prepare(
    settings: Settings, feature: AiFeature | None = None
) -> tuple[VendorDescriptor, Callable[[], DraftModel]]:
    descriptor = describe(settings, feature)
    endpoint, _model, _source = _resolved(settings, feature)
    # Authoritative egress check, before anything is constructed. Resolves the
    # name and validates EVERY address it answers with.
    try:
        check_url(endpoint, field=_URL_FIELD)
    except OutboundTargetBlocked as blocked:
        raise VendorUnavailable(VENDOR, "endpoint_invalid") from blocked
    if GoogleCredentialSettings().gemini_api_key is None:
        raise VendorUnavailable(VENDOR, "key_missing")
    return descriptor, lambda: GoogleModel(settings, feature=feature)


class GoogleModel:
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
        credentials = GoogleCredentialSettings()
        if credentials.gemini_api_key is None:
            raise VendorUnavailable(VENDOR, "key_missing")
        self._settings = settings
        self._feature: AiFeature | None = feature
        self._key = credentials.gemini_api_key
        self._endpoint, self._model, _source = _resolved(settings, feature)
        #: Test seam (``httpx.MockTransport``). No test reaches the network.
        self._transport = transport

    @property
    def descriptor(self) -> VendorDescriptor:
        return describe(self._settings, self._feature)

    # -- request ------------------------------------------------------------

    def _payload(self, request: ModelRequest[Any]) -> dict[str, Any]:
        ai = self._settings.ai
        return {
            "systemInstruction": {
                "parts": [{"text": block.text} for block in request.system],
            },
            "contents": [{"role": "user", "parts": [{"text": request.user_content}]}],
            "generationConfig": {
                "responseMimeType": _JSON_MIME,
                _SCHEMA_FIELD: request.output_type.model_json_schema(),
                "maxOutputTokens": ai.max_output_tokens,
            },
        }

    def _client(self) -> httpx.Client:
        import httpx  # noqa: PLC0415 - the one lazy transport import in this adapter

        ai = self._settings.ai
        return httpx.Client(
            headers={
                # Header, not a query parameter: a key in a URL lands in every
                # proxy log between here and the vendor.
                "x-goog-api-key": self._key.get_secret_value(),
                "Content-Type": _JSON_MIME,
            },
            timeout=ai.request_timeout_seconds,
            transport=self._transport or httpx.HTTPTransport(retries=ai.max_retries),
            follow_redirects=False,
            event_hooks={"response": [redirect_guard(field=_URL_FIELD)]},
        )

    def generate[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        import httpx  # noqa: PLC0415 - the one lazy transport import in this adapter

        started = time.monotonic()
        # Re-checked immediately before the socket opens: settings can change
        # between prepare and here, and this is the layer that stops egress.
        try:
            check_url(self._endpoint, field=_URL_FIELD)
        except OutboundTargetBlocked:
            return self._failure("endpoint_invalid", started)
        try:
            with self._client() as client:
                response = client.post(self._endpoint, json=self._payload(request))
        except OutboundTargetBlocked:
            # A redirect to a destination the guard refuses.
            return self._failure("endpoint_invalid", started)
        except httpx.TimeoutException:
            return self._failure("timeout", started)
        except httpx.TransportError:
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
        outcome = "rate_limited" if code == "rate_limited" else "failed"
        return ModelResult(
            outcome=outcome, failure_code=code, request_id=request_id, **self._common(started)
        )

    def _from_status(self, response: httpx.Response, started: float) -> ModelResult[Any]:
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
            # RESOURCE_EXHAUSTED covers both the rate limit and a finished
            # billing quota; both are availability and both fail over.
            return self._failure("rate_limited", started, request_id=request_id)
        if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            return self._failure("not_configured", started, request_id=request_id)
        if status == HTTPStatus.NOT_FOUND:
            return self._failure("model_unavailable", started, request_id=request_id)
        # ``>=`` and no upper bound: an HTTP status is three digits, so there is
        # nothing above 599 to exclude, and naming the floor through the enum
        # keeps this free of the numeric literals D-024 forbids in AI code.
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return self._failure("upstream_error", started, request_id=request_id)
        return self._failure("bad_request", started, request_id=request_id)

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
            "model_served": body.get("modelVersion"),
            "request_id": body.get("responseId") or response.headers.get("x-request-id"),
            "usage": _usage(body.get("usageMetadata")),
        }

        # Refusal FIRST, and the candidate's text is never read on that path.
        blocked = (body.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            return ModelResult(
                outcome="refused", refusal_category=str(blocked), stop_reason=str(blocked), **common
            )
        candidates = body.get("candidates") or ()
        if not candidates or not isinstance(candidates[0], dict):
            return ModelResult(outcome="schema_invalid", failure_code="no_parsed_output", **common)
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish == _FINISH_TRUNCATED:
            return ModelResult(
                outcome="truncated", failure_code="max_tokens", stop_reason=finish, **common
            )
        if finish and finish != _FINISH_OK:
            return ModelResult(
                outcome="refused", refusal_category=str(finish), stop_reason=str(finish), **common
            )

        text = "".join(
            str(part.get("text") or "")
            for part in (candidate.get("content") or {}).get("parts") or ()
            if isinstance(part, dict)
        )
        if not text:
            return ModelResult(outcome="schema_invalid", failure_code="no_parsed_output", **common)
        try:
            parsed = request.output_type.model_validate_json(text)
        except Exception:  # noqa: BLE001 - a validation failure is an outcome
            return ModelResult(outcome="schema_invalid", failure_code="parse_failed", **common)
        return ModelResult(outcome="ok", parsed=parsed, stop_reason=finish, **common)


def _usage(raw: Any) -> UsageRecord | None:
    """This vendor's token counts, mapped onto the shared record."""
    if not isinstance(raw, dict):
        return None
    return UsageRecord(
        input_tokens=raw.get("promptTokenCount"),
        output_tokens=raw.get("candidatesTokenCount"),
        cache_read_input_tokens=raw.get("cachedContentTokenCount"),
    )


__all__ = [
    "VENDOR",
    "GoogleCredentialSettings",
    "GoogleEndpointSettings",
    "GoogleModel",
    "describe",
    "prepare",
]
