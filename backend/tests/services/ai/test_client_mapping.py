"""How the client reads a response — and what it refuses to read.

The order in ``_from_response`` is the safety property: refusal FIRST, branching
on ``stop_reason`` and never on ``stop_details`` (null for every other stop), and
the content is never touched on that path. Everything else is mapping.

No test here touches the network: the SDK object is a stub and the exception
classes are the real ``anthropic`` ones constructed by hand.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.ai import client as ai_client


class _Draft(BaseModel):
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    inference_geo: str | None = None
    iterations: tuple[Any, ...] = ()


@dataclass
class _StubIteration:
    type: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class _StubStopDetails:
    category: str | None


class _StubResponse:
    def __init__(self, **kwargs: Any) -> None:
        self.model = kwargs.get("model", "claude-opus-5")
        self.stop_reason = kwargs.get("stop_reason", "end_turn")
        self.stop_details = kwargs.get("stop_details")
        self.usage = kwargs.get("usage", _StubUsage())
        self._request_id = kwargs.get("request_id", "req_123")
        self._parsed = kwargs.get("parsed", _Draft(text="ok"))
        self._parsed_raises = kwargs.get("parsed_raises", False)
        self.content = kwargs.get("content", [])

    @property
    def parsed_output(self) -> Any:
        if self._parsed_raises:
            message = "schema mismatch"
            raise ValueError(message)
        return self._parsed


class _Model(ai_client.AnthropicModel):
    """AnthropicModel with the constructor skipped: no SDK client, no key."""

    def __init__(self) -> None:  # noqa: PLW3201 - deliberately bypasses the base
        import anthropic  # noqa: PLC0415 - the real exception classes

        self._anthropic = anthropic
        self._settings = get_settings()
        self._client = None  # type: ignore[assignment]


@pytest.fixture
def model() -> _Model:
    return _Model()


def test_refusal_is_checked_before_content_is_read(model: _Model) -> None:
    """A refused response's content is never touched — only its category."""
    response = _StubResponse(
        stop_reason="refusal",
        stop_details=_StubStopDetails(category="cyber"),
        parsed_raises=True,  # reading the content would blow up
    )
    result = model._from_response(response, 0.0)  # noqa: SLF001
    assert result.outcome == "refused"
    assert result.refusal_category == "cyber"
    assert result.parsed is None


def test_refusal_with_null_stop_details_is_still_a_refusal(model: _Model) -> None:
    """Branch on stop_reason, never on stop_details, which can be null."""
    result = model._from_response(  # noqa: SLF001
        _StubResponse(stop_reason="refusal", stop_details=None), 0.0
    )
    assert result.outcome == "refused"
    assert result.refusal_category is None


def test_max_tokens_is_truncated_not_parsed(model: _Model) -> None:
    """A truncated structured output has nothing honest to parse out of it."""
    result = model._from_response(  # noqa: SLF001
        _StubResponse(stop_reason="max_tokens", parsed_raises=True), 0.0
    )
    assert result.outcome == "truncated"
    assert result.failure_code == "max_tokens"
    assert result.parsed is None


def test_schema_failure_is_an_outcome_not_an_exception(model: _Model) -> None:
    assert (
        model._from_response(_StubResponse(parsed_raises=True), 0.0).outcome  # noqa: SLF001
        == "schema_invalid"
    )
    assert (
        model._from_response(_StubResponse(parsed=None), 0.0).outcome  # noqa: SLF001
        == "schema_invalid"
    )


def test_ok_carries_the_parsed_object(model: _Model) -> None:
    result = model._from_response(_StubResponse(), 0.0)  # noqa: SLF001
    assert result.outcome == "ok"
    assert isinstance(result.parsed, _Draft)
    assert result.request_id == "req_123"


def test_fallback_is_detected_from_usage_iterations(model: _Model) -> None:
    """The served-by signal, which covers sticky turns carrying no content block."""
    usage = _StubUsage(
        iterations=(
            _StubIteration(type="message", model="claude-opus-5"),
            _StubIteration(type="fallback_message", model="claude-opus-4-8"),
        )
    )
    result = model._from_response(  # noqa: SLF001
        _StubResponse(usage=usage, model="claude-opus-4-8"), 0.0
    )
    assert result.fallback_used is True
    assert result.model_served == "claude-opus-4-8"


def test_no_fallback_when_only_ordinary_iterations(model: _Model) -> None:
    usage = _StubUsage(iterations=(_StubIteration(type="message"),))
    assert model._from_response(_StubResponse(usage=usage), 0.0).fallback_used is False  # noqa: SLF001


def test_usage_records_cache_tokens_and_inference_geography(model: _Model) -> None:
    """Geography is recorded, never requested: there is no African region."""
    usage = _StubUsage(
        cache_creation_input_tokens=800,
        cache_read_input_tokens=1200,
        inference_geo="us",
    )
    result = model._from_response(_StubResponse(usage=usage), 0.0)  # noqa: SLF001
    assert result.usage is not None
    assert result.usage.cache_read_input_tokens == 1200
    assert result.usage.inference_geo == "us"


# --- exception mapping ------------------------------------------------------


def _response(status_code: int) -> httpx2.Response:
    # httpx2, not httpx: the SDK is built on it, and the exception constructors
    # are typed against its Response/Request.
    return httpx2.Response(
        status_code,
        headers={"request-id": "req_err"},
        request=httpx2.Request("POST", "https://api.invalid/v1/messages"),
    )


@pytest.mark.parametrize(
    ("exception_name", "status_code", "outcome", "failure_code"),
    [
        ("RateLimitError", 429, "rate_limited", "rate_limited"),
        ("AuthenticationError", 401, "failed", "not_configured"),
        ("PermissionDeniedError", 403, "failed", "not_configured"),
        ("NotFoundError", 404, "failed", "model_unavailable"),
        ("BadRequestError", 400, "failed", "bad_request"),
        ("InternalServerError", 500, "failed", "upstream_error"),
    ],
)
def test_status_exceptions_map_to_outcomes(
    model: _Model, exception_name: str, status_code: int, outcome: str, failure_code: str
) -> None:
    import anthropic  # noqa: PLC0415

    cls = getattr(anthropic, exception_name)
    exc = cls("boom", response=_response(status_code), body=None)
    result = model._from_exception(exc, 0.0)  # noqa: SLF001
    assert (result.outcome, result.failure_code) == (outcome, failure_code)
    assert result.request_id == "req_err"


def test_overloaded_is_rate_limited_not_a_failure(model: _Model) -> None:
    """529 means try later, which is the same story a user needs as a 429."""
    import anthropic  # noqa: PLC0415

    exc = anthropic.APIStatusError("overloaded", response=_response(529), body=None)
    result = model._from_exception(exc, 0.0)  # noqa: SLF001
    assert (result.outcome, result.failure_code) == ("rate_limited", "overloaded")


def test_timeout_is_matched_before_its_connection_base_class(model: _Model) -> None:
    import anthropic  # noqa: PLC0415

    request = httpx2.Request("POST", "https://api.invalid/v1/messages")
    assert (
        model._from_exception(  # noqa: SLF001
            anthropic.APITimeoutError(request=request), 0.0
        ).failure_code
        == "timeout"
    )
    assert (
        model._from_exception(  # noqa: SLF001
            anthropic.APIConnectionError(request=request), 0.0
        ).failure_code
        == "connection"
    )


def test_a_programming_error_still_propagates(model: _Model) -> None:
    """Only API outcomes are swallowed; a bug must not become a 'failed' row."""
    with pytest.raises(ZeroDivisionError):
        model._from_exception(ZeroDivisionError("bug"), 0.0)  # noqa: SLF001


# --- request shape ----------------------------------------------------------


def test_forbidden_sampling_parameters_appear_nowhere_in_the_request() -> None:
    """temperature/top_p/top_k/budget_tokens are all 400s on Opus 5."""
    source = inspect.getsource(ai_client.AnthropicModel.generate)
    for banned in ("temperature", "top_p", "top_k", "budget_tokens", "inference_geo"):
        assert banned not in source


def test_the_fallback_beta_is_the_scalar_form_header() -> None:
    """Pairing the array-form header with fallbacks="default" is a 400."""
    assert ai_client.SERVER_SIDE_FALLBACK_BETA == "server-side-fallback-2026-07-01"


def test_system_blocks_carry_cache_control_in_order(model: _Model) -> None:
    request = ai_client.ModelRequest(
        feature="icaap_drafting",
        system=(
            ai_client.SystemBlock(text="static", cache=True),
            ai_client.SystemBlock(text="addendum", cache=True),
        ),
        user_content="{}",
        output_type=_Draft,
    )
    blocks = model._system_blocks(request)  # noqa: SLF001
    assert [block["text"] for block in blocks] == ["static", "addendum"]
    assert all("cache_control" in block for block in blocks)
    assert blocks[0]["cache_control"]["ttl"] == get_settings().ai.prompt_cache_ttl


def test_an_uncached_block_carries_no_cache_control(model: _Model) -> None:
    request = ai_client.ModelRequest(
        feature="icaap_drafting",
        system=(ai_client.SystemBlock(text="volatile", cache=False),),
        user_content="{}",
        output_type=_Draft,
    )
    assert "cache_control" not in model._system_blocks(request)[0]  # noqa: SLF001
