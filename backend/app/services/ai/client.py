"""The Anthropic adapter, and the provider-agnostic contract every adapter fills.

Two jobs in one file, deliberately: ``ModelRequest``/``ModelResult``/
``DraftModel`` are the seam every vendor adapter implements (``openai_model``,
``google_model``), and ``AnthropicModel`` is the first vendor's implementation of
it — kept here because this is the module the architecture test pins as the ONE
importer of the ``anthropic`` SDK. ``tiered`` walks the adapters; nothing else
constructs one.

Three properties this file exists to hold:

1. **The SDK is imported lazily, inside ``AnthropicModel.__init__``.** The API
   process, the core worker and the operator app must never load it, and an
   architecture test pins that ``import app.main`` leaves ``anthropic`` out of
   ``sys.modules``.
2. **The key is read by a settings class of its own**, instantiated only when a
   real model is constructed, so no other process parses it into memory even if
   it were present in that container's environment. The API key is passed to the
   SDK EXPLICITLY — never the zero-argument constructor, which would silently
   pick up a developer's ``ant auth login`` profile and make a local test call a
   real model on someone's account.
3. **``generate`` never raises for an API outcome.** Every SDK exception and
   every non-``end_turn`` stop becomes an ``Outcome``. A refusal is a status the
   user sees, not an exception the worker retries — retrying a refusal would
   double-spend and duplicate a sealed row.

API shape (verified against the ``claude-api`` skill and ``anthropic`` 1.7.0 at
implementation time, 2026-09-19):

    client.beta.messages.parse(
        model=..., max_tokens=..., system=[...], messages=[...],
        thinking={"type": "adaptive"},
        output_config={"effort": ...},
        output_format=SectionDraft,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )

``parse`` merges ``output_format`` into ``output_config.format`` itself, so both
may be passed together; ``fallbacks`` accepts the literal ``"default"`` and that
scalar form REQUIRES the ``-2026-07-01`` beta (the ``-2026-06-01`` header gates
the older array form, and pairing either header with the other form is a 400).
Never set ``temperature``/``top_p``/``top_k`` or ``budget_tokens``: all four are
rejected with a 400 on Opus 5.

Three of the things this request does are Anthropic-only and do NOT transfer to
another vendor: ``cache_control`` on the static system blocks, the server-side
``fallbacks="default"`` beta, and ``thinking={"type": "adaptive"}``. That is why
the other adapters record them as degraded capabilities on every draft rather
than quietly sending a weaker request (D-053).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from app.core.config import (
    SETTINGS_CONFIG,
    AiVendor,
    Settings,
    get_settings,
    is_undeployed_environment,
)
from app.services.ai.features import AiFeature
from app.services.ai.vendors import VendorDescriptor, VendorUnavailable, resolve_effort

if TYPE_CHECKING:  # pragma: no cover - typing only
    from typing import Literal

T = TypeVar("T", bound=BaseModel)

#: The beta that gates the ``fallbacks: "default"`` SCALAR form. An API contract
#: constant, not a tunable: the value is fixed by the provider, and pairing it
#: with the array form is a 400.
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: This module's vendor (audit A7-02). Every adapter exposes the same
#: module-level name — ``openai_model.VENDOR``, ``google_model.VENDOR`` — so the
#: three can be handled uniformly, which is exactly how the tier walks them.
#: ``AnthropicModel.VENDOR`` mirrors this rather than restating it, so the two
#: can never drift apart.
VENDOR: AiVendor = "anthropic"


class RealModelForbiddenError(RuntimeError):
    """A real model client was constructed where none may exist.

    Raised by the test-suite guard and by the production refusal of the recorded
    backend. Both are programming/configuration errors, not user-facing states.
    """


class AiCredentialSettings(BaseSettings):
    """The API key, read nowhere else.

    Deliberately NOT part of ``Settings``: it is instantiated only inside
    ``AnthropicModel.__init__``, so the key never enters the memory of a process
    that does not call the model.
    """

    model_config = SETTINGS_CONFIG

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def empty_means_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@dataclass(frozen=True)
class SystemBlock:
    """One system block. ``cache`` marks it for the prompt cache."""

    text: str
    cache: bool


@dataclass(frozen=True)
class ModelRequest[T: BaseModel]:
    """A feature-agnostic request. BI commentary will build these too."""

    feature: AiFeature
    #: The caller's prompt version. Part of the approved-configuration key, so the
    #: request has to carry it: the tier checks the approval PER VENDOR before it
    #: opens a socket, and it has nothing else to look the approval up by.
    prompt_version: str
    #: Stable prefix first, per-section addendum second. The volatile fact sheet
    #: is the user turn, so the cached prefix is never disturbed by it.
    system: tuple[SystemBlock, ...]
    user_content: str
    output_type: type[T]


@dataclass(frozen=True)
class UsageRecord:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    #: Recorded, never requested: there is no African inference region, so the
    #: served geography is evidence a bank's supervisor may ask for.
    inference_geo: str | None = None
    iterations: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "inference_geo": self.inference_geo,
            "iterations": list(self.iterations),
        }


if TYPE_CHECKING:  # pragma: no cover - typing only
    Outcome = Literal["ok", "refused", "truncated", "schema_invalid", "rate_limited", "failed"]
else:
    Outcome = str

#: Outcomes whose model output must NEVER be stored or shown. A refusal produced
#: no draft; a rate limit produced nothing at all.
NO_OUTPUT_OUTCOMES: frozenset[str] = frozenset({"refused", "rate_limited", "failed", "truncated"})


@dataclass(frozen=True)
class ModelResult[T: BaseModel]:
    outcome: Outcome
    model_requested: str
    parsed: T | None = None
    model_served: str | None = None
    fallback_used: bool = False
    request_id: str | None = None
    stop_reason: str | None = None
    refusal_category: str | None = None
    failure_code: str | None = None
    usage: UsageRecord | None = None
    latency_ms: int = 0
    #: Which vendor produced this, and where it sat in ``AI_PROVIDER_TIER``. Load
    #: bearing since D-053: identical inputs now yield different text depending on
    #: who was up, so a reader of a FILED report must be able to tell which.
    vendor: str | None = None
    tier_position: int | None = None
    #: Where ``model_requested`` came from (D-061): ``feature_override`` or
    #: ``vendor_default``. Recorded on the call, not in the filed provenance dict,
    #: because it is how the deployment was configured rather than part of what the
    #: document says about itself.
    model_source: str | None = None
    #: Request features this vendor could not honour (``vendors.CAP_*``).
    degraded: tuple[str, ...] = ()
    #: Every vendor tried before this one: ``{vendor, tier_position, outcome,
    #: failure_code, failure_class}``. Empty when the first vendor answered.
    tier_attempts: tuple[dict[str, Any], ...] = ()

    def call_record(self) -> dict[str, Any]:
        """The audit record of the call: token usage PLUS vendor provenance.

        ALWAYS a dict, even when the vendor returned no usage block, because the
        provenance of a failed or refused call is evidence too — and the tier
        that produced it is the part a supervisor would ask about.
        """
        record = self.usage.as_dict() if self.usage is not None else _EMPTY_USAGE.as_dict()
        record.update(
            {
                "vendor": self.vendor,
                "model_requested": self.model_requested,
                "model_served": self.model_served,
                "model_source": self.model_source,
                "tier_position": self.tier_position,
                "degraded_capabilities": list(self.degraded),
                "tier_attempts": list(self.tier_attempts),
            }
        )
        return record


#: The shape ``call_record`` falls back to, so a failed call still records the
#: same keys as a successful one instead of a differently-shaped dict.
_EMPTY_USAGE: UsageRecord = UsageRecord()


class DraftModel(Protocol):
    def generate(self, request: ModelRequest[T]) -> ModelResult[T]: ...


_OVERRIDE: ContextVar[DraftModel | None] = ContextVar("ai_model_override", default=None)


@contextmanager
def use_model(model: DraftModel) -> Iterator[None]:
    """Override the model for a test or an eval. Never used in product code."""
    token = _OVERRIDE.set(model)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


#: What a replayed draft records as its vendor. A fixture is not a vendor, and a
#: recorded run must not read like one answered — D-053 requires the stamp on
#: EVERY draft, including this path.
RECORDED_VENDOR = "recorded"


class RecordedModel:
    """A canned model. Records every request so tests can assert on the prompt."""

    def __init__(
        self,
        results: Sequence[ModelResult[Any]] | Callable[[ModelRequest[Any]], ModelResult[Any]],
    ) -> None:
        self._results = results
        self._index = 0
        self.requests: list[ModelRequest[Any]] = []

    def generate(self, request: ModelRequest[T]) -> ModelResult[T]:
        self.requests.append(request)
        if callable(self._results):
            return self._stamped(self._results(request))
        if self._index >= len(self._results):
            message = "RecordedModel ran out of canned results"
            raise AssertionError(message)
        result = self._results[self._index]
        self._index += 1
        return self._stamped(result)

    def _stamped(self, result: ModelResult[Any]) -> ModelResult[Any]:
        """Fill in the provenance a canned result left out, never overwrite it.

        A tier test cans a result that already names its vendor; a plain fixture
        replay does not, and gets ``recorded`` at position one rather than a blank
        where the provenance should be.
        """
        if result.vendor is not None:
            return result
        return replace(result, vendor=RECORDED_VENDOR, tier_position=1)


class AnthropicModel:
    """The real client. Constructed only on the AI worker."""

    #: Tier position 1 in the default order, and the reference implementation of
    #: every request feature — so nothing here is ever a degraded capability.
    #: Mirrors the module-level constant; never a second literal.
    VENDOR: AiVendor = VENDOR

    @property
    def descriptor(self) -> VendorDescriptor:
        """What this adapter will send. A property, not a constructor field, so a
        test may bypass ``__init__`` and still describe the request."""
        return describe(self._settings, getattr(self, "_feature", None))

    def __init__(
        self, settings: Settings | None = None, *, feature: AiFeature | None = None
    ) -> None:
        settings = settings or get_settings()
        self._feature: AiFeature | None = feature
        credentials = AiCredentialSettings()
        if credentials.anthropic_api_key is None:
            message = "ANTHROPIC_API_KEY is not configured for this process."
            raise RealModelForbiddenError(message)
        import anthropic  # noqa: PLC0415 - the one lazy SDK import in the codebase

        from app.core.tls import client_context  # noqa: PLC0415

        self._anthropic = anthropic
        self._settings = settings
        # Explicit api_key: the zero-argument constructor would fall through to
        # an ``ant auth login`` profile on a developer's machine and bill a real
        # account from what was meant to be a local run.
        self._client = anthropic.Anthropic(
            api_key=credentials.anthropic_api_key.get_secret_value(),
            base_url="https://api.anthropic.com",
            http_client=anthropic.DefaultHttpxClient(
                verify=client_context(), trust_env=False, follow_redirects=False
            ),
            timeout=settings.ai.request_timeout_seconds,
            max_retries=settings.ai.max_retries,
        )

    def _system_blocks(self, request: ModelRequest[T]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for block in request.system:
            entry: dict[str, Any] = {"type": "text", "text": block.text}
            if block.cache:
                entry["cache_control"] = {
                    "type": "ephemeral",
                    "ttl": self._settings.ai.prompt_cache_ttl,
                }
            blocks.append(entry)
        return blocks

    def generate(self, request: ModelRequest[T]) -> ModelResult[T]:  # noqa: PLR0911
        ai = self._settings.ai
        # The DESCRIPTOR is what the socket sends — never ``ai.model`` (audit
        # A7-01). The approval gate, ``ModelResult.model_requested`` and the
        # filed ICAAP provenance all read ``descriptor.model``; sending anything
        # else means the configuration that was approved and the configuration
        # that ran are two different things, and the filed evidence names the
        # wrong one. Both HTTP adapters already do this (``openai_model.py:180``,
        # ``google_model.py``); tier 1 was the outlier.
        descriptor = self.descriptor
        kwargs: dict[str, Any] = {
            "model": descriptor.model,
            "max_tokens": ai.max_output_tokens,
            "system": self._system_blocks(request),
            "messages": [{"role": "user", "content": request.user_content}],
            # Explicit, although it is Opus 5's default: an unstated default is
            # one migration away from changing under us. ``display`` stays
            # omitted — we never want the reasoning text.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": descriptor.effort},
            "output_format": request.output_type,
        }
        if ai.fallbacks == "default":
            kwargs["betas"] = [SERVER_SIDE_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        started = time.monotonic()
        try:
            response = self._client.beta.messages.parse(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to an outcome below
            return self._from_exception(exc, started)
        return self._from_response(response, started)

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    def _usage(self, response: Any) -> UsageRecord | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        iterations = tuple(
            {
                "type": getattr(entry, "type", None),
                "model": getattr(entry, "model", None),
                "input_tokens": getattr(entry, "input_tokens", None),
                "output_tokens": getattr(entry, "output_tokens", None),
            }
            for entry in (getattr(usage, "iterations", None) or ())
        )
        return UsageRecord(
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            inference_geo=getattr(usage, "inference_geo", None),
            iterations=iterations,
        )

    def _from_response(self, response: Any, started: float) -> ModelResult[Any]:
        usage = self._usage(response)
        # ``fallback_message`` in usage.iterations is the served-by signal and
        # covers sticky routing, which carries no ``fallback`` content block.
        fallback_used = any(
            entry.get("type") == "fallback_message" for entry in (usage.iterations if usage else ())
        )
        descriptor = self.descriptor
        common: dict[str, Any] = {
            "model_requested": descriptor.model,
            "model_served": getattr(response, "model", None),
            "fallback_used": fallback_used,
            "request_id": getattr(response, "_request_id", None),
            "stop_reason": getattr(response, "stop_reason", None),
            "usage": usage,
            "latency_ms": self._elapsed_ms(started),
            "vendor": descriptor.vendor,
            "degraded": descriptor.degraded,
            "model_source": descriptor.model_source,
        }
        stop_reason = common["stop_reason"]

        # Refusal FIRST, and branch on stop_reason, never on stop_details, which
        # is null for every other stop reason. The content is NEVER read.
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return ModelResult(
                outcome="refused",
                refusal_category=getattr(details, "category", None) if details else None,
                **common,
            )
        if stop_reason == "max_tokens":
            # Structured output is incomplete by definition; there is nothing
            # honest to parse out of a truncated schema.
            return ModelResult(outcome="truncated", failure_code="max_tokens", **common)

        try:
            parsed = response.parsed_output
        except Exception:  # noqa: BLE001 - a validation failure is an outcome
            return ModelResult(outcome="schema_invalid", failure_code="parse_failed", **common)
        if parsed is None:
            return ModelResult(outcome="schema_invalid", failure_code="no_parsed_output", **common)
        return ModelResult(outcome="ok", parsed=parsed, **common)

    def _from_exception(  # noqa: PLR0911 - one return per SDK exception class
        self, exc: Exception, started: float
    ) -> ModelResult[Any]:
        anthropic = self._anthropic
        request_id = None
        response = getattr(exc, "response", None)
        if response is not None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                request_id = headers.get("request-id")
        descriptor = self.descriptor
        common: dict[str, Any] = {
            "model_requested": descriptor.model,
            "request_id": request_id,
            "latency_ms": self._elapsed_ms(started),
            "vendor": descriptor.vendor,
            "degraded": descriptor.degraded,
            "model_source": descriptor.model_source,
        }
        overloaded_status = 529
        # Most specific first. APITimeoutError before APIConnectionError: it is
        # a subclass, and a timeout is a different operational story.
        if isinstance(exc, anthropic.RateLimitError):
            return ModelResult(outcome="rate_limited", failure_code="rate_limited", **common)
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return ModelResult(outcome="failed", failure_code="not_configured", **common)
        if isinstance(exc, anthropic.NotFoundError):
            return ModelResult(outcome="failed", failure_code="model_unavailable", **common)
        if isinstance(exc, anthropic.BadRequestError):
            return ModelResult(outcome="failed", failure_code="bad_request", **common)
        if isinstance(exc, anthropic.APITimeoutError):
            return ModelResult(outcome="failed", failure_code="timeout", **common)
        if isinstance(exc, anthropic.APIConnectionError):
            return ModelResult(outcome="failed", failure_code="connection", **common)
        if isinstance(exc, anthropic.APIStatusError):
            if getattr(exc, "status_code", None) == overloaded_status:
                return ModelResult(outcome="rate_limited", failure_code="overloaded", **common)
            return ModelResult(outcome="failed", failure_code="upstream_error", **common)
        raise exc


def describe(settings: Settings, feature: AiFeature | None = None) -> VendorDescriptor:
    """Anthropic's descriptor: the reference implementation, nothing degraded.

    Credential-free on purpose — the enqueue gate calls this in the API process.
    ``feature`` selects the model (D-061); ``None`` asks the vendor-default
    question, which is what the run gate's "can this process call anything" needs.
    """
    from app.services.ai import model_selection  # noqa: PLC0415 - avoids an import cycle

    resolved = model_selection.resolve(feature, AnthropicModel.VENDOR, settings)
    effort, _downgraded = resolve_effort(AnthropicModel.VENDOR, settings.ai.effort)
    return VendorDescriptor(
        vendor=AnthropicModel.VENDOR,
        model=resolved.model,
        effort=effort,
        degraded=(),
        model_source=resolved.source,
    )


def prepare(
    settings: Settings, feature: AiFeature | None = None
) -> tuple[VendorDescriptor, Callable[[], DraftModel]]:
    """The tier's entry point for this vendor: describe first, build on demand.

    Split in two so the tier can check the per-vendor approved configuration and
    the credential BEFORE anything is constructed — a vendor with no key is
    skipped by name, never by a caught exception from deep inside an SDK.
    """
    descriptor = describe(settings, feature)
    if AiCredentialSettings().anthropic_api_key is None:
        raise VendorUnavailable(AnthropicModel.VENDOR, "key_missing")
    return descriptor, lambda: AnthropicModel(settings, feature=feature)


def backend_configured(settings: Settings | None = None) -> bool:
    """Can THIS process actually call a model?

    Asked at the run gate only. ``recorded`` is never configured in a deployed
    environment — replaying a fixture in staging would look exactly like a
    working feature while sending nothing and grounding nothing.

    An active ``use_model`` override counts as configured: it IS what this
    process will call. That keeps the question honest in tests and the offline
    eval harness, and changes nothing in production, where no override exists.

    Since D-053 the question is "is ANY vendor in the tier usable", because the
    whole point of the tier is that one vendor being out of credit is not the
    feature being unconfigured.
    """
    if _OVERRIDE.get() is not None:
        return True
    settings = settings or get_settings()
    if settings.ai.model_backend == "recorded":
        return is_undeployed_environment(settings.app.app_env)
    from app.services.ai import tiered  # noqa: PLC0415 - avoids an import cycle

    return bool(tiered.configured_vendors(settings))


def get_model(settings: Settings | None = None) -> DraftModel:
    """The model this process should use, honouring a test/eval override."""
    override = _OVERRIDE.get()
    if override is not None:
        return override
    settings = settings or get_settings()
    if settings.ai.model_backend == "recorded":
        if not is_undeployed_environment(settings.app.app_env):
            message = "The recorded AI backend is refused outside local and test."
            raise RealModelForbiddenError(message)
        return _recorded_from_fixture(settings)
    from app.services.ai import tiered  # noqa: PLC0415 - avoids an import cycle

    # ``anthropic`` pins the tier to one vendor in settings, so both real
    # backends take the same path and the provenance stamp is never skipped.
    return tiered.TieredModel(settings)


def _recorded_from_fixture(settings: Settings) -> RecordedModel:
    import json  # noqa: PLC0415 - only the fixture path needs it
    from pathlib import Path  # noqa: PLC0415

    path = settings.ai.recorded_fixture_path
    if path is None:
        message = "AI_MODEL_BACKEND=recorded needs AI_RECORDED_FIXTURE_PATH."
        raise RealModelForbiddenError(message)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    results = [
        ModelResult(
            outcome=entry.get("outcome", "ok"),
            model_requested=settings.ai.model,
            model_served=entry.get("model_served", settings.ai.model),
            parsed=entry.get("parsed"),
            stop_reason=entry.get("stop_reason"),
            refusal_category=entry.get("refusal_category"),
            failure_code=entry.get("failure_code"),
        )
        for entry in raw.get("results", [])
    ]
    return RecordedModel(results)


def complete_structured[R: BaseModel](
    request: ModelRequest[R], settings: Settings | None = None
) -> ModelResult[R]:
    """THE provider-agnostic entry point. One prompt, one schema, one result.

    Every AI feature calls this and nothing else. Which vendor served it, where
    that vendor sat in the tier, and what it could not honour come back stamped
    on the result; whether to fail over is decided in ``vendors``, never here and
    never by a caller.
    """
    return get_model(settings).generate(request)


__all__ = [
    "NO_OUTPUT_OUTCOMES",
    "RECORDED_VENDOR",
    "SERVER_SIDE_FALLBACK_BETA",
    "AiCredentialSettings",
    "AnthropicModel",
    "DraftModel",
    "ModelRequest",
    "ModelResult",
    "Outcome",
    "RealModelForbiddenError",
    "RecordedModel",
    "SystemBlock",
    "UsageRecord",
    "backend_configured",
    "complete_structured",
    "describe",
    "get_model",
    "prepare",
    "use_model",
]
