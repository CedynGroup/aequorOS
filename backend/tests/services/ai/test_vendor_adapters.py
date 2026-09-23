"""The OpenAI and Gemini adapters: credentials, egress, and outcome mapping.

Every request here goes through ``httpx.MockTransport``, so no test reaches the
network — and the egress guard's DNS seam (``outbound.resolve_host``) is
monkeypatched, because the guard RESOLVES and a test that let it would do real
lookups. The constructors are reached by bypassing the suite-wide guard on
purpose, which is the only place in the suite that is allowed to.

The mapping tests assert the one thing that decides a failover: HTTP status ->
failure code. A body is read in exactly one place (OpenAI's ``error.code``) and
only to sharpen a 429 that already fails over, so the assertions prove the
decision survives a vendor rewording its messages.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core import outbound
from app.core.config import get_settings
from app.services.ai import client as ai_client
from app.services.ai import google_model, openai_model, vendors

_GEMINI_URL = "https://generativelanguage.example.com/v1beta/models/gemini-x:generateContent"

#: The pristine constructors, captured at IMPORT time — before the suite-wide
#: guard replaces them. Reading them inside a fixture would capture the guard
#: itself, because the autouse fixture runs first.
_PRISTINE_INIT = {
    openai_model.OpenAiModel: openai_model.OpenAiModel.__init__,
    google_model.GoogleModel: google_model.GoogleModel.__init__,
}


class _Draft(BaseModel):
    text: str


def _request() -> ai_client.ModelRequest[_Draft]:
    return ai_client.ModelRequest(
        feature="icaap_drafting",
        prompt_version="test-prompt-v1",
        system=(
            ai_client.SystemBlock(text="static", cache=True),
            ai_client.SystemBlock(text="addendum", cache=True),
        ),
        user_content='{"facts": []}',
        output_type=_Draft,
    )


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name resolves to one routable address. No real lookup happens."""
    monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.114.9",))


@pytest.fixture
def real_clients_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the suite-wide construction guard for THIS module only.

    The guard exists so a forgotten fixture cannot bill somebody's account; these
    tests need the real constructor and give it a stub transport, which is the
    narrow case it was always meant to allow.
    """
    for real, original in _PRISTINE_INIT.items():
        monkeypatch.setattr(real, "__init__", original)


def _openai(monkeypatch: pytest.MonkeyPatch, handler: Any) -> openai_model.OpenAiModel:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    get_settings.cache_clear()
    return openai_model.OpenAiModel(get_settings(), transport=httpx.MockTransport(handler))


def _google(monkeypatch: pytest.MonkeyPatch, handler: Any) -> google_model.GoogleModel:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv("AI_GOOGLE_MODEL", "")
    get_settings.cache_clear()
    return google_model.GoogleModel(get_settings(), transport=httpx.MockTransport(handler))


def _json(status: int, body: dict[str, Any]) -> Any:
    return lambda request: httpx.Response(status, json=body)


# --- credentials ------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings_class", "field", "env"),
    [
        (openai_model.OpenAiCredentialSettings, "openai_api_key", "OPENAI_API_KEY"),
        (google_model.GoogleCredentialSettings, "gemini_api_key", "GEMINI_API_KEY"),
    ],
)
def test_each_vendor_key_is_a_secret_in_a_class_of_its_own(
    monkeypatch: pytest.MonkeyPatch, settings_class: Any, field: str, env: str
) -> None:
    """Same pattern as ``AiCredentialSettings``: SecretStr, local to the adapter,
    so no process that does not call the vendor parses it, and no repr leaks it."""
    monkeypatch.setenv(env, "secret-value-do-not-log")
    credentials = settings_class()
    assert "secret-value-do-not-log" not in repr(credentials)
    assert getattr(credentials, field).get_secret_value() == "secret-value-do-not-log"


def test_no_vendor_key_is_part_of_the_settings_aggregate() -> None:
    settings = get_settings()
    dumped = str(settings.model_dump()).casefold()
    for name in ("openai_api_key", "gemini_api_key", "anthropic_api_key"):
        assert name not in dumped


@pytest.mark.parametrize(
    ("module", "env", "code"),
    [
        (openai_model, "OPENAI_API_KEY", "key_missing"),
        (google_model, "GEMINI_API_KEY", "key_missing"),
    ],
)
def test_a_missing_key_skips_the_vendor_by_name(
    monkeypatch: pytest.MonkeyPatch, public_dns: None, module: Any, env: str, code: str
) -> None:
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv(env, "")
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        module.prepare(get_settings())
    assert raised.value.code == code


def test_the_key_is_sent_explicitly_on_the_request(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    """Never a client's environment fallback: the value on the wire is the value
    the adapter read, from the variable the adapter names."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(HTTPStatus.OK, json=_openai_ok_body())

    _openai(monkeypatch, handler).generate(_request())
    assert seen["authorization"] == "Bearer sk-test-not-real"


def test_the_gemini_key_travels_in_a_header_not_the_url(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    """A key in a query string lands in every proxy log on the way."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        return httpx.Response(HTTPStatus.OK, json=_gemini_ok_body())

    _google(monkeypatch, handler).generate(_request())
    assert seen["key"] == "gm-test-not-real"
    assert "gm-test-not-real" not in seen["url"]


# --- the Gemini URL is an egress surface ------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://generativelanguage.googleapis.com/v1beta",  # plain http
        "https://127.0.0.1/v1beta",  # loopback
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://10.1.2.3/v1beta",  # RFC1918
        "https://localhost/v1beta",  # blocked name
    ],
)
def test_a_gemini_url_that_fails_the_egress_guard_skips_the_vendor(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """The operator supplies this URL, so it is an SSRF surface. It goes through
    ``app.core.outbound`` — the same authoritative guard the Database-Direct,
    Temenos and ORASS connectors use — and a refusal skips the tier by name
    rather than sending anything anywhere."""
    monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.114.9",))
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", url)
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        google_model.prepare(get_settings())
    assert raised.value.code == "endpoint_invalid"


def test_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The syntax check alone is not the control: the resolving layer is."""
    monkeypatch.setattr(outbound, "resolve_host", lambda host: ("192.168.4.4",))
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", "https://gemini.internal.example/v1beta")
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        google_model.prepare(get_settings())
    assert raised.value.code == "endpoint_invalid"


def test_a_permitted_gemini_url_prepares(monkeypatch: pytest.MonkeyPatch, public_dns: None) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv("AI_GOOGLE_MODEL", "")
    get_settings.cache_clear()
    descriptor, _build = google_model.prepare(get_settings())
    # With AI_GOOGLE_MODEL unset the endpoint's own model id is authoritative.
    assert (descriptor.vendor, descriptor.model) == ("google", "gemini-x")


def test_the_configured_model_is_pinned_into_the_endpoint(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    """An approved configuration pins a model id; the URL must not quietly call a
    different one."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(HTTPStatus.OK, json=_gemini_ok_body())

    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv("AI_GOOGLE_MODEL", "gemini-pinned")
    get_settings.cache_clear()
    model = google_model.GoogleModel(get_settings(), transport=httpx.MockTransport(handler))
    model.generate(_request())
    assert seen["url"].endswith("/v1beta/models/gemini-pinned:generateContent")


def test_a_missing_gemini_url_skips_the_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_URL", "")
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        google_model.describe(get_settings())
    assert raised.value.code == "endpoint_missing"


def test_an_unnamed_model_skips_the_vendor_rather_than_guessing_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_URL", "https://generativelanguage.example.com/v1beta")
    monkeypatch.setenv("AI_GOOGLE_MODEL", "")
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        google_model.describe(get_settings())
    assert raised.value.code == "model_unconfigured"


def test_an_unnamed_openai_model_skips_the_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_OPENAI_MODEL", "")
    get_settings.cache_clear()
    with pytest.raises(vendors.VendorUnavailable) as raised:
        openai_model.describe(get_settings())
    assert raised.value.code == "model_unconfigured"


# --- status mapping ---------------------------------------------------------


def _openai_ok_body() -> dict[str, Any]:
    return {
        "id": "resp_abc",
        "model": "gpt-test",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"text": "hello"}'}],
            },
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "input_tokens_details": {"cached_tokens": 64},
        },
    }


def _gemini_ok_body() -> dict[str, Any]:
    return {
        "modelVersion": "gemini-test",
        "responseId": "resp_xyz",
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": '{"text": "hello"}'}]},
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 130,
            "candidatesTokenCount": 45,
            "cachedContentTokenCount": 0,
        },
    }


_STATUS_CASES = [
    (HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "rate_limited"),
    (HTTPStatus.UNAUTHORIZED, "failed", "not_configured"),
    (HTTPStatus.FORBIDDEN, "failed", "not_configured"),
    (HTTPStatus.NOT_FOUND, "failed", "model_unavailable"),
    (HTTPStatus.INTERNAL_SERVER_ERROR, "failed", "upstream_error"),
    (HTTPStatus.SERVICE_UNAVAILABLE, "failed", "upstream_error"),
    (HTTPStatus.BAD_REQUEST, "failed", "bad_request"),
]


@pytest.mark.parametrize(("status", "outcome", "code"), _STATUS_CASES)
def test_openai_status_maps_to_an_outcome(
    monkeypatch: pytest.MonkeyPatch,
    real_clients_allowed: None,
    status: HTTPStatus,
    outcome: str,
    code: str,
) -> None:
    result = _openai(monkeypatch, _json(status, {"error": {"type": "x"}})).generate(_request())
    assert (result.outcome, result.failure_code) == (outcome, code)
    assert result.vendor == "openai"


@pytest.mark.parametrize(("status", "outcome", "code"), _STATUS_CASES)
def test_gemini_status_maps_to_an_outcome(  # noqa: PLR0913 - three fixtures + three cases
    monkeypatch: pytest.MonkeyPatch,
    real_clients_allowed: None,
    public_dns: None,
    status: HTTPStatus,
    outcome: str,
    code: str,
) -> None:
    result = _google(monkeypatch, _json(status, {"error": {"status": "x"}})).generate(_request())
    assert (result.outcome, result.failure_code) == (outcome, code)
    assert result.vendor == "google"


#: Status codes no ``HTTPStatus`` member defines, and every one of them is
#: something a real vendor edge returns under load: 520 "unknown error", 521
#: "origin down", 522 "connection timed out", 524 "a timeout occurred", 526
#: "invalid SSL certificate" and 529 "site overloaded" are Cloudflare's, and 499
#: "client closed request" is nginx's. ``HTTPStatus(520)`` raises ValueError, so
#: constructing the enum meant the most obvious availability failure there is
#: escaped ``generate`` as an exception instead of failing over (audit A7-05).
_NON_ENUM_SERVER_ERRORS = [520, 521, 522, 524, 526, 529]


@pytest.mark.parametrize("status", _NON_ENUM_SERVER_ERRORS)
@pytest.mark.parametrize("vendor", ["openai", "google"])
def test_a_status_the_enum_does_not_define_still_fails_over(
    monkeypatch: pytest.MonkeyPatch,
    real_clients_allowed: None,
    public_dns: None,
    vendor: str,
    status: int,
) -> None:
    """An edge 5xx is an availability failure at every vendor, enum or not.

    Before the fix this raised ``ValueError: 520 is not a valid HTTPStatus`` out
    of ``generate``, so the tier never saw a failure it could advance past and
    the caller's job row was stranded mid-chain with its attempts spent.
    """
    model = (
        _openai(monkeypatch, _json(status, {"error": {"type": "x"}}))
        if vendor == "openai"
        else _google(monkeypatch, _json(status, {"error": {"status": "x"}}))
    )
    result = model.generate(_request())
    assert (result.outcome, result.failure_code) == ("failed", "upstream_error")
    assert result.vendor == vendor
    assert vendors.failure_class(result.failure_code) == "availability"


@pytest.mark.parametrize("vendor", ["openai", "google"])
def test_a_client_status_the_enum_does_not_define_is_still_terminal(
    monkeypatch: pytest.MonkeyPatch,
    real_clients_allowed: None,
    public_dns: None,
    vendor: str,
) -> None:
    """499 is nginx's, and it is OUR request that died — not the vendor being
    down. It must stay terminal, or a malformed call would be retried at every
    vendor in the tier."""
    model = (
        _openai(monkeypatch, _json(499, {"error": {"type": "x"}}))
        if vendor == "openai"
        else _google(monkeypatch, _json(499, {"error": {"status": "x"}}))
    )
    result = model.generate(_request())
    assert (result.outcome, result.failure_code) == ("failed", "bad_request")
    assert vendors.failure_class(result.failure_code) != "availability"


@pytest.mark.parametrize(("status", "outcome", "code"), _STATUS_CASES)
def test_every_status_code_is_classified_and_only_availability_fails_over(
    status: HTTPStatus, outcome: str, code: str
) -> None:
    """The mapping and the failover set have to agree, or a 400 tours the vendors
    and a 429 does not."""
    expected = "terminal" if code == "bad_request" else "availability"
    if code == "model_unavailable":
        expected = "configuration"
    assert vendors.failure_class(code) == expected


def test_out_of_credit_is_recorded_more_precisely_than_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    """Both fail over; the machine code only sharpens the audit row. No failover
    decision depends on a string the vendor could reword."""
    body = {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}}
    result = _openai(monkeypatch, _json(HTTPStatus.TOO_MANY_REQUESTS, body)).generate(_request())
    assert (result.outcome, result.failure_code) == ("rate_limited", "insufficient_quota")
    assert result.failure_code in vendors.AVAILABILITY_FAILURE_CODES


def test_a_429_with_an_unreadable_body_is_still_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    handler = lambda request: httpx.Response(HTTPStatus.TOO_MANY_REQUESTS, content=b"<html>")  # noqa: E731
    result = _openai(monkeypatch, handler).generate(_request())
    assert (result.outcome, result.failure_code) == ("rate_limited", "rate_limited")


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("no route"), "connection"),
    ],
)
def test_transport_faults_map_to_availability_codes(
    monkeypatch: pytest.MonkeyPatch,
    real_clients_allowed: None,
    raised: Exception,
    code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    result = _openai(monkeypatch, handler).generate(_request())
    assert result.failure_code == code
    assert code in vendors.AVAILABILITY_FAILURE_CODES


# --- response mapping -------------------------------------------------------


def test_openai_ok_parses_into_the_requested_schema(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    result = _openai(monkeypatch, _json(HTTPStatus.OK, _openai_ok_body())).generate(_request())
    assert result.outcome == "ok"
    assert isinstance(result.parsed, _Draft)
    assert result.parsed.text == "hello"
    assert (result.model_served, result.request_id) == ("gpt-test", "resp_abc")
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.cache_read_input_tokens) == (120, 64)


def test_openai_refusal_is_a_status_and_its_text_is_never_read(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    body = {
        "id": "resp_r",
        "model": "gpt-test",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "I cannot help with that"}],
            }
        ],
    }
    result = _openai(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert (result.outcome, result.parsed) == ("refused", None)


def test_openai_truncation_is_not_parsed(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    body = {
        "id": "resp_t",
        "model": "gpt-test",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"te'}]}],
    }
    result = _openai(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert (result.outcome, result.failure_code, result.parsed) == (
        "truncated",
        "max_output_tokens",
        None,
    )


def test_openai_output_that_does_not_fit_the_schema_is_an_outcome(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    body = _openai_ok_body()
    body["output"][1]["content"][0]["text"] = '{"wrong_field": 1}'
    result = _openai(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert (result.outcome, result.failure_code) == ("schema_invalid", "parse_failed")


def test_gemini_ok_parses_into_the_requested_schema(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    result = _google(monkeypatch, _json(HTTPStatus.OK, _gemini_ok_body())).generate(_request())
    assert result.outcome == "ok"
    assert isinstance(result.parsed, _Draft)
    assert (result.model_served, result.request_id) == ("gemini-test", "resp_xyz")
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (130, 45)


@pytest.mark.parametrize("finish", ["SAFETY", "PROHIBITED_CONTENT", "RECITATION"])
def test_gemini_reports_a_block_as_a_refusal(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None, finish: str
) -> None:
    """This vendor reports a safety block as a finish reason rather than an
    outcome of its own; it is still a refusal, and its text is still never read."""
    body = _gemini_ok_body()
    body["candidates"][0]["finishReason"] = finish
    result = _google(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert (result.outcome, result.refusal_category, result.parsed) == ("refused", finish, None)


def test_gemini_prompt_feedback_block_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    body = {"promptFeedback": {"blockReason": "SAFETY"}, "modelVersion": "gemini-test"}
    result = _google(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert result.outcome == "refused"


def test_gemini_truncation_is_not_parsed(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    body = _gemini_ok_body()
    body["candidates"][0]["finishReason"] = "MAX_TOKENS"
    result = _google(monkeypatch, _json(HTTPStatus.OK, body)).generate(_request())
    assert (result.outcome, result.failure_code, result.parsed) == ("truncated", "max_tokens", None)


def test_gemini_with_no_candidate_is_a_schema_outcome(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    result = _google(monkeypatch, _json(HTTPStatus.OK, {"candidates": []})).generate(_request())
    assert (result.outcome, result.failure_code) == ("schema_invalid", "no_parsed_output")


# --- degraded capabilities are recorded, not dropped ------------------------


def test_the_openai_request_carries_the_schema_and_the_mapped_effort(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json  # noqa: PLC0415 - reading what the adapter sent

        sent.update(json.loads(request.content))
        return httpx.Response(HTTPStatus.OK, json=_openai_ok_body())

    monkeypatch.setenv("AI_EFFORT", "max")
    result = _openai(monkeypatch, handler).generate(_request())

    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["schema"]["properties"]["text"]["type"] == "string"
    # ``max`` has no native equivalent, so it resolves down — and says so.
    assert sent["reasoning"]["effort"] == "high"
    assert vendors.CAP_EFFORT_LEVEL in result.degraded
    # No per-block cache instruction exists to send: the blocks are concatenated
    # and the omission is on the record.
    assert sent["instructions"] == "static\n\naddendum"
    assert vendors.CAP_PROMPT_CACHE in result.degraded


def test_the_gemini_request_carries_the_schema_and_no_thinking_field(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    sent: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json  # noqa: PLC0415 - reading what the adapter sent

        sent.update(json.loads(request.content))
        return httpx.Response(HTTPStatus.OK, json=_gemini_ok_body())

    result = _google(monkeypatch, handler).generate(_request())

    config = sent["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["properties"]["text"]["type"] == "string"
    assert "thinkingConfig" not in config
    assert vendors.CAP_ADAPTIVE_THINKING in result.degraded
    assert vendors.CAP_EFFORT_LEVEL in result.degraded


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (openai_model, {vendors.CAP_PROMPT_CACHE, vendors.CAP_ADAPTIVE_THINKING}),
        (
            google_model,
            {
                vendors.CAP_PROMPT_CACHE,
                vendors.CAP_ADAPTIVE_THINKING,
                vendors.CAP_EFFORT_LEVEL,
            },
        ),
    ],
)
def test_anthropic_only_behaviour_degrades_visibly(
    monkeypatch: pytest.MonkeyPatch, module: Any, expected: set[str]
) -> None:
    """Prompt caching, the server-side fallback beta and adaptive thinking do not
    transfer. They must be a recorded, inspectable fact on every draft rather than
    something that silently stopped happening."""
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv("AI_GOOGLE_MODEL", "")
    monkeypatch.setenv("AI_FALLBACKS", "default")
    get_settings.cache_clear()
    degraded = set(module.describe(get_settings()).degraded)
    assert expected <= degraded
    assert vendors.CAP_SERVER_SIDE_FALLBACKS in degraded
    assert degraded <= vendors.DEGRADABLE_CAPABILITIES


def test_the_reference_vendor_degrades_nothing() -> None:
    assert ai_client.describe(get_settings()).degraded == ()
