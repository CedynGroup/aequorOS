"""The grounding validator, proven against EACH vendor's output shape (D-053).

The validator was written when one vendor's structured output was the only shape
that existed, and "it will be the same object after parsing" is exactly the kind
of assumption that is true right up until a filed ICAAP report contains a bare
number. So every vendor is run end to end on a REALISTIC response payload for
that vendor — the shape its own API documents, not a normalised stand-in — and
the same two drafts are asserted through all three: one grounded draft that must
validate, one with a bare digit that must be refused with the same code.

The second half is the property that protects a bank's quota and a bank's report
at the same time: an ungrounded draft is an ANSWER, so the tier must stop on it.
A vendor that produced unusable prose has still been paid; asking the next two
for the same thing would spend three times as much to fail three times.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from app.core import outbound
from app.core.config import get_settings
from app.domain.ai import grounding as grounding_domain
from app.domain.ai.lexicon import load_lexicon
from app.schemas.icaap_ai import SectionDraft
from app.services.ai import client as ai_client
from app.services.ai import google_model, openai_model, tiered, vendors
from app.services.ai.grounding import limits_from_settings

_GEMINI_URL = "https://generativelanguage.example.com/v1beta/models/gemini-x:generateContent"

#: Captured before the suite-wide guard replaces them (see test_vendor_adapters).
_PRISTINE_INIT = {
    openai_model.OpenAiModel: openai_model.OpenAiModel.__init__,
    google_model.GoogleModel: google_model.GoogleModel.__init__,
}

#: A draft that obeys every rule: figures only as ``{{F:id}}``, names only as
#: ``{{E:key}}``, and the one regulatory term the lexicon allows to carry a digit.
GROUNDED_DRAFT: dict[str, Any] = {
    "paragraphs": [
        {
            "text": (
                "The Board of {{E:bank}} reviewed the capital adequacy ratio of "
                "{{F:capital.car_pct}}, which remains above the minimum applied by "
                "{{E:regulator}} as at {{E:as_of}}."
            ),
            "requirement_ids": ["R1"],
        },
        {
            "text": (
                "Common Equity Tier 1 capital carries the whole of the surplus, and "
                "management considers the buffer adequate under Pillar 2."
            ),
            "requirement_ids": [],
        },
    ],
    "open_questions": ["Confirm which committee approved the current appetite statement."],
}

#: The same draft with the one thing the validator exists to catch: a figure
#: written out instead of referenced.
UNGROUNDED_DRAFT: dict[str, Any] = {
    "paragraphs": [
        {
            "text": "The capital adequacy ratio stood at 14.2 per cent at the reporting date.",
            "requirement_ids": ["R1"],
        }
    ],
    "open_questions": [],
}


@pytest.fixture
def real_clients_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    for real, original in _PRISTINE_INIT.items():
        monkeypatch.setattr(real, "__init__", original)


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.114.9",))


def _context() -> grounding_domain.GroundingContext:
    """A grounding context built by hand: no database, no tenant."""
    return grounding_domain.GroundingContext(
        facts={"capital.car_pct": True, "capital.tier1_pct": False},
        entities=frozenset({"bank", "regulator", "as_of"}),
        requirement_ids=frozenset({"R1", "R2"}),
        deny_terms=frozenset({"Sample Bank"}),
        jurisdiction_terms=frozenset({"Ghana", "Bank of Ghana"}),
        lexicon=load_lexicon(),
    )


def _validate(draft: SectionDraft) -> grounding_domain.GroundingResult:
    """Exactly what ``icaap/ai_jobs`` does with a parsed draft."""
    output = draft.model_dump()
    paragraphs = [
        (str(item["text"]), [str(value) for value in item["requirement_ids"]])
        for item in output["paragraphs"]
    ]
    return grounding_domain.validate(
        paragraphs, [str(q) for q in output["open_questions"]], _context(), limits_from_settings()
    )


def _request() -> ai_client.ModelRequest[SectionDraft]:
    return ai_client.ModelRequest(
        feature="icaap_drafting",
        prompt_version="test-prompt-v1",
        system=(ai_client.SystemBlock(text="static", cache=True),),
        user_content='{"facts": []}',
        output_type=SectionDraft,
    )


# --- one realistic payload per vendor ---------------------------------------


def _anthropic_parsed(draft: dict[str, Any]) -> Any:
    """This vendor's SDK hands back an instance of the pydantic output type."""
    return SectionDraft.model_validate(draft)


def _openai_body(draft: dict[str, Any]) -> dict[str, Any]:
    """The Responses API shape: a reasoning item, then a message whose single
    ``output_text`` part is the JSON document."""
    import json  # noqa: PLC0415 - building a realistic vendor payload

    return {
        "id": "resp_68c1",
        "object": "response",
        "model": "gpt-test",
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(draft), "annotations": []}],
            },
        ],
        "usage": {
            "input_tokens": 2048,
            "output_tokens": 311,
            "input_tokens_details": {"cached_tokens": 1920},
            "total_tokens": 2359,
        },
    }


def _gemini_body(draft: dict[str, Any]) -> dict[str, Any]:
    """The ``generateContent`` shape: one candidate whose single part is the JSON
    document, because ``responseMimeType`` was ``application/json``."""
    import json  # noqa: PLC0415 - building a realistic vendor payload

    return {
        "modelVersion": "gemini-test",
        "responseId": "Zx8gaM",
        "candidates": [
            {
                "index": 0,
                "finishReason": "STOP",
                "content": {"role": "model", "parts": [{"text": json.dumps(draft)}]},
                "safetyRatings": [],
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 2100,
            "candidatesTokenCount": 305,
            "totalTokenCount": 2405,
        },
    }


def _parse_openai(
    monkeypatch: pytest.MonkeyPatch, draft: dict[str, Any]
) -> ai_client.ModelResult[Any]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    get_settings.cache_clear()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(HTTPStatus.OK, json=_openai_body(draft))
    )
    model = openai_model.OpenAiModel(get_settings(), transport=transport)
    return model.generate(_request())


def _parse_gemini(
    monkeypatch: pytest.MonkeyPatch, draft: dict[str, Any]
) -> ai_client.ModelResult[Any]:
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-not-real")
    monkeypatch.setenv("GEMINI_API_URL", _GEMINI_URL)
    monkeypatch.setenv("AI_GOOGLE_MODEL", "")
    get_settings.cache_clear()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(HTTPStatus.OK, json=_gemini_body(draft))
    )
    model = google_model.GoogleModel(get_settings(), transport=transport)
    return model.generate(_request())


# --- the grounded draft validates on every vendor ---------------------------


def test_anthropic_output_validates() -> None:
    assert _validate(_anthropic_parsed(GROUNDED_DRAFT)).ok


def test_openai_output_validates(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    result = _parse_openai(monkeypatch, GROUNDED_DRAFT)
    assert result.outcome == "ok"
    assert isinstance(result.parsed, SectionDraft)
    assert _validate(result.parsed).ok


def test_gemini_output_validates(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    result = _parse_gemini(monkeypatch, GROUNDED_DRAFT)
    assert result.outcome == "ok"
    assert isinstance(result.parsed, SectionDraft)
    assert _validate(result.parsed).ok


def test_every_vendor_parses_to_the_same_object(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    """The point of proving the validator per vendor: after each adapter, the
    thing the validator sees is byte-identical, so one validator is enough."""
    parsed = [
        _anthropic_parsed(GROUNDED_DRAFT).model_dump(),
        _parse_openai(monkeypatch, GROUNDED_DRAFT).parsed.model_dump(),  # type: ignore[union-attr]
        _parse_gemini(monkeypatch, GROUNDED_DRAFT).parsed.model_dump(),  # type: ignore[union-attr]
    ]
    assert parsed[0] == parsed[1] == parsed[2]


# --- the ungrounded draft is refused on every vendor ------------------------


def test_anthropic_ungrounded_output_is_refused() -> None:
    verdict = _validate(_anthropic_parsed(UNGROUNDED_DRAFT))
    assert not verdict.ok
    assert "digit" in verdict.codes


def test_openai_ungrounded_output_is_refused(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None
) -> None:
    result = _parse_openai(monkeypatch, UNGROUNDED_DRAFT)
    verdict = _validate(result.parsed)  # type: ignore[arg-type]
    assert not verdict.ok
    assert "digit" in verdict.codes


def test_gemini_ungrounded_output_is_refused(
    monkeypatch: pytest.MonkeyPatch, real_clients_allowed: None, public_dns: None
) -> None:
    result = _parse_gemini(monkeypatch, UNGROUNDED_DRAFT)
    verdict = _validate(result.parsed)  # type: ignore[arg-type]
    assert not verdict.ok
    assert "digit" in verdict.codes


def test_an_ungrounded_draft_never_reaches_the_next_vendor() -> None:
    """The rule D-053 states in as many words. A draft that fails grounding goes
    to the deterministic fallback, not to another provider."""
    calls: list[str] = []

    class _Vendor:
        def __init__(self, name: str) -> None:
            self.name = name

        def generate(self, request: ai_client.ModelRequest[Any]) -> Any:
            calls.append(self.name)
            return ai_client.ModelResult(
                outcome="ok",
                model_requested=f"{self.name}-model",
                parsed=SectionDraft.model_validate(UNGROUNDED_DRAFT),
                vendor=self.name,
            )

    def _prepare(name: str) -> Any:
        def _inner(settings: Any) -> Any:
            descriptor = vendors.VendorDescriptor(
                vendor=name,  # type: ignore[arg-type]
                model=f"{name}-model",
                effort=settings.ai.effort,
            )
            return descriptor, lambda: _Vendor(name)

        return _inner

    prepares = {name: _prepare(name) for name in ("anthropic", "openai", "google")}
    result = tiered.TieredModel(get_settings(), prepares=prepares).generate(_request())

    assert calls == ["anthropic"], "an ungrounded draft must cost exactly one call"
    assert result.outcome == "ok"
    # And the caller is the one that refuses it.
    assert not _validate(result.parsed).ok  # type: ignore[arg-type]
