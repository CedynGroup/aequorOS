"""Nothing the feature exists to protect may appear in a log line."""

from __future__ import annotations

import logging

import pytest

from app.services.ai import observability


def test_an_unknown_field_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="allow-list"):
        observability.log_ai_event("ai.call.completed", fact_sheet={"secret": 1})


def test_planted_sentinels_never_reach_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The four things the boundary exists for: sheet, values, prompt, output."""
    caplog.set_level(logging.INFO, logger="app.ai")
    observability.log_ai_event(
        observability.EVENT_COMPLETED,
        feature="icaap_drafting",
        suggestion_id="0199a3c0-0000-7000-8000-000000000001",
        organization_id="OR-DEM00001",
        status="validated",
        outcome="ok",
        model_served="claude-opus-5",
        input_tokens=100,
        output_tokens=50,
    )
    rendered = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    for sentinel in ("SENTINEL_SHEET", "SENTINEL_VALUE", "SENTINEL_PROMPT", "SENTINEL_OUTPUT"):
        assert sentinel not in rendered


def test_the_allow_list_carries_no_content_field() -> None:
    """A field that could hold prose has no business on this list."""
    forbidden = {
        "fact_sheet",
        "facts",
        "prompt",
        "system",
        "output",
        "paragraphs",
        "open_questions",
        "text",
        "label",
        "value",
        "entity_values",
        "bank_name",
    }
    assert not (observability.ALLOWED_FIELDS & forbidden)


def test_a_cold_cache_on_a_stable_prefix_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A zero cache read means a silent invalidator is costing money quietly."""
    caplog.set_level(logging.WARNING, logger="app.ai")
    observability.log_cache_miss_if_cold(
        cache_read_input_tokens=0,
        feature="icaap_drafting",
        prompt_version="icaap-draft-v1",
    )
    assert any(record.levelno == logging.WARNING for record in caplog.records)

    caplog.clear()
    observability.log_cache_miss_if_cold(
        cache_read_input_tokens=1200,
        feature="icaap_drafting",
        prompt_version="icaap-draft-v1",
    )
    assert not caplog.records


def test_the_cache_miss_helper_enforces_the_same_allow_list() -> None:
    with pytest.raises(ValueError, match="allow-list"):
        observability.log_cache_miss_if_cold(cache_read_input_tokens=0, prompt="leak")
