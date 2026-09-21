"""The model client: the ONLY module that imports the Anthropic SDK.

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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from app.core.config import SETTINGS_CONFIG, Settings, get_settings, is_undeployed_environment
from app.services.ai.features import AiFeature

if TYPE_CHECKING:  # pragma: no cover - typing only
    from typing import Literal

T = TypeVar("T", bound=BaseModel)

#: The beta that gates the ``fallbacks: "default"`` SCALAR form. An API contract
#: constant, not a tunable: the value is fixed by the provider, and pairing it
#: with the array form is a 400.
SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"


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
            return self._results(request)
        if self._index >= len(self._results):
            message = "RecordedModel ran out of canned results"
            raise AssertionError(message)
        result = self._results[self._index]
        self._index += 1
        return result


class AnthropicModel:
    """The real client. Constructed only on the AI worker."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        credentials = AiCredentialSettings()
        if credentials.anthropic_api_key is None:
            message = "ANTHROPIC_API_KEY is not configured for this process."
            raise RealModelForbiddenError(message)
        import anthropic  # noqa: PLC0415 - the one lazy SDK import in the codebase

        self._anthropic = anthropic
        self._settings = settings
        # Explicit api_key: the zero-argument constructor would fall through to
        # an ``ant auth login`` profile on a developer's machine and bill a real
        # account from what was meant to be a local run.
        self._client = anthropic.Anthropic(
            api_key=credentials.anthropic_api_key.get_secret_value(),
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
        kwargs: dict[str, Any] = {
            "model": ai.model,
            "max_tokens": ai.max_output_tokens,
            "system": self._system_blocks(request),
            "messages": [{"role": "user", "content": request.user_content}],
            # Explicit, although it is Opus 5's default: an unstated default is
            # one migration away from changing under us. ``display`` stays
            # omitted — we never want the reasoning text.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": ai.effort},
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
        common: dict[str, Any] = {
            "model_requested": self._settings.ai.model,
            "model_served": getattr(response, "model", None),
            "fallback_used": fallback_used,
            "request_id": getattr(response, "_request_id", None),
            "stop_reason": getattr(response, "stop_reason", None),
            "usage": usage,
            "latency_ms": self._elapsed_ms(started),
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
        common: dict[str, Any] = {
            "model_requested": self._settings.ai.model,
            "request_id": request_id,
            "latency_ms": self._elapsed_ms(started),
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


def backend_configured(settings: Settings | None = None) -> bool:
    """Can THIS process actually call a model?

    Asked at the run gate only. ``recorded`` is never configured in a deployed
    environment — replaying a fixture in staging would look exactly like a
    working feature while sending nothing and grounding nothing.

    An active ``use_model`` override counts as configured: it IS what this
    process will call. That keeps the question honest in tests and the offline
    eval harness, and changes nothing in production, where no override exists.
    """
    if _OVERRIDE.get() is not None:
        return True
    settings = settings or get_settings()
    if settings.ai.model_backend == "recorded":
        return is_undeployed_environment(settings.app.app_env)
    return AiCredentialSettings().anthropic_api_key is not None


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
    return AnthropicModel(settings)


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


__all__ = [
    "NO_OUTPUT_OUTCOMES",
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
    "get_model",
    "use_model",
]
