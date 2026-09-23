"""Per-feature model selection (D-061): what pins, what may float, what is refused.

The reason this is per feature and not one global id: an ICAAP narrative rides a
FILED regulatory document, so if its model moves then identical inputs produce
different text while the provenance stamped into that document's evidence
describes something that changed underneath it. BI commentary is advisory and
regenerable, so it may track a vendor's floating alias.

The test that matters most is the last section: **an override must not be able to
route around ``approved_configurations.json``.** The approval key contains the
model, so if the lookup used the vendor default while the request carried an
override, an unreviewed model would be admitted by an approval nobody gave it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import AI_VENDORS, get_settings
from app.services.ai import (
    approvals,
    gates,
    google_model,
    model_selection,
    openai_model,
    tiered,
    vendors,
)
from app.services.ai import client as ai_client
from app.services.ai.features import AI_FEATURES

_GEMINI_URL = "https://generativelanguage.example.com/v1beta/models/gemini-x:generateContent"
_PROMPT = "test-prompt-v1"


class _Draft(BaseModel):
    text: str


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return get_settings()


# --- resolution order -------------------------------------------------------


def test_with_no_override_every_feature_takes_the_vendor_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility property: a deployment that has only ever set the three
    vendor-level ids behaves exactly as it did before D-061."""
    settings = _settings(monkeypatch, AI_FEATURE_MODELS="")
    for feature in AI_FEATURES:
        resolved = model_selection.resolve(feature, "anthropic", settings)
        assert resolved.model == settings.ai.model
        assert resolved.source == "vendor_default"


def test_a_feature_override_wins_over_the_vendor_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch, AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-snapshot-2026-04-01"
    )
    pinned = model_selection.resolve("icaap_drafting", "anthropic", settings)
    assert (pinned.model, pinned.source) == ("claude-snapshot-2026-04-01", "feature_override")


def test_an_override_does_not_leak_to_another_feature_or_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch, AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-snapshot-2026-04-01"
    )
    other_feature = model_selection.resolve("bi_commentary", "anthropic", settings)
    other_vendor = model_selection.resolve("icaap_drafting", "openai", settings)
    assert other_feature.source == "vendor_default"
    assert other_vendor.model == settings.ai.openai_model


def test_a_featureless_question_resolves_the_vendor_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run gate asks "can this process call anything at all", which no pinning
    rule can be applied to — there is no feature to apply it for."""
    settings = _settings(
        monkeypatch, AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-snapshot-2026-04-01"
    )
    resolved = model_selection.resolve(None, "anthropic", settings)
    assert (resolved.model, resolved.policy) == (settings.ai.model, None)


def test_an_unconfigured_slot_skips_the_vendor_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, AI_OPENAI_MODEL="", AI_FEATURE_MODELS="")
    with pytest.raises(vendors.VendorUnavailable) as raised:
        model_selection.resolve("bi_commentary", "openai", settings)
    assert raised.value.code == "model_unconfigured"


# --- the policy -------------------------------------------------------------


def test_every_feature_declares_whether_its_model_may_move() -> None:
    """A new AI surface cannot be added without that decision being made."""
    assert sorted(model_selection.FEATURE_MODEL_POLICY) == sorted(AI_FEATURES)


def test_icaap_pins_and_bi_commentary_may_float() -> None:
    assert model_selection.policy_for("icaap_drafting") == "pinned"
    assert model_selection.policy_for("bi_commentary") == "may_float"


def test_a_pinned_feature_refuses_a_floating_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filed document's model must not be "whatever is newest"."""
    settings = _settings(monkeypatch, AI_FEATURE_MODELS="icaap_drafting:openai=gpt-family-latest")
    with pytest.raises(vendors.VendorUnavailable) as raised:
        model_selection.resolve("icaap_drafting", "openai", settings)
    assert raised.value.code == "model_not_pinned"
    assert raised.value.code in vendors.CONFIGURATION_FAILURE_CODES


def test_an_advisory_feature_may_track_a_floating_id(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, AI_FEATURE_MODELS="bi_commentary:openai=gpt-family-latest")
    resolved = model_selection.resolve("bi_commentary", "openai", settings)
    assert (resolved.model, resolved.floating) == ("gpt-family-latest", True)


def test_a_floating_id_is_refused_for_a_vendor_that_publishes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic publishes undated GENERATION ids but no cross-generation
    "latest", so pointing a feature at one would be the platform inventing an id
    the vendor does not serve — caught here rather than as a 404 in production."""
    assert "anthropic" not in model_selection.VENDORS_PUBLISHING_FLOATING_IDS
    settings = _settings(monkeypatch, AI_FEATURE_MODELS="bi_commentary:anthropic=claude-latest")
    with pytest.raises(vendors.VendorUnavailable) as raised:
        model_selection.resolve("bi_commentary", "anthropic", settings)
    assert raised.value.code == "model_alias_unsupported"


def test_an_override_naming_an_unknown_feature_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grammar and the vendor are validated at boot; the FEATURE name is
    checked here, because a typo would otherwise silently mean "no override"."""
    settings = _settings(monkeypatch, AI_FEATURE_MODELS="icap_drafting:anthropic=claude-x")
    with pytest.raises(vendors.VendorUnavailable) as raised:
        model_selection.resolve("icaap_drafting", "anthropic", settings)
    assert raised.value.code == "model_override_invalid"


# --- the resolved id reaches the descriptor, the stamp and the endpoint ------


def test_each_adapter_describes_the_resolved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        GEMINI_API_URL=_GEMINI_URL,
        AI_FEATURE_MODELS=(
            "icaap_drafting:anthropic=claude-pinned,"
            "icaap_drafting:openai=gpt-pinned,"
            "icaap_drafting:google=gemini-pinned"
        ),
    )
    described = {
        module.VENDOR: module.describe(settings, "icaap_drafting")
        for module in (ai_client, openai_model, google_model)
    }
    assert described["anthropic"].model == "claude-pinned"
    assert described["openai"].model == "gpt-pinned"
    assert described["google"].model == "gemini-pinned"
    assert {entry.model_source for entry in described.values()} == {"feature_override"}


def test_a_vendor_the_feature_cannot_use_describes_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the enqueue gate sees: undescribable is treated exactly like
    unapproved, because it could not be called either."""
    _settings(monkeypatch, AI_FEATURE_MODELS="icaap_drafting:openai=gpt-family-latest")
    assert tiered.describe("openai", get_settings(), "icaap_drafting") is None
    assert tiered.describe("openai", get_settings(), "bi_commentary") is not None


def test_the_gemini_endpoint_carries_the_features_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GEMINI_API_URL`` is the endpoint, not the choice: the id an approval pins
    is the id the request carries."""
    settings = _settings(
        monkeypatch,
        GEMINI_API_URL=_GEMINI_URL,
        AI_GOOGLE_MODEL="",
        AI_FEATURE_MODELS="icaap_drafting:google=gemini-pinned",
    )
    endpoint, model, source = google_model._resolved(settings, "icaap_drafting")  # noqa: SLF001
    assert endpoint.endswith("/v1beta/models/gemini-pinned:generateContent")
    assert (model, source) == ("gemini-pinned", "feature_override")
    # With no override and no AI_GOOGLE_MODEL, the URL's own id still stands in.
    _endpoint, fallback, fallback_source = google_model._resolved(settings, "bi_commentary")  # noqa: SLF001
    assert (fallback, fallback_source) == ("gemini-x", "vendor_default")


def test_the_call_record_says_where_the_model_id_came_from() -> None:
    result = ai_client.ModelResult(
        outcome="ok",
        model_requested="claude-pinned",
        vendor="anthropic",
        tier_position=1,
        model_source="feature_override",
    )
    record = result.call_record()
    assert (record["model_requested"], record["model_source"]) == (
        "claude-pinned",
        "feature_override",
    )


# --- an override CANNOT route around approval -------------------------------


def _entry(vendor: str, model: str, effort: str) -> Any:
    return approvals.ApprovedConfiguration(
        feature="icaap_drafting",
        prompt_version=_PROMPT,
        vendor=vendor,
        model=model,
        effort=effort,
        app_env="production",
        eval_report_sha256=None,
        approved_by=None,
        approved_on=None,
        reference=None,
    )


def test_an_override_naming_an_unapproved_model_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline rule. An approval for the VENDOR DEFAULT must not admit a
    feature override that points somewhere else — the approval key contains the
    model, and the lookup has to use the id the request will actually carry."""
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        AI_COMMENTARY_ENABLED="1",
        AI_PRODUCTION_APPROVAL_REF="EVAL-2026-09-A",
        AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-not-reviewed",
    )
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("anthropic", settings.ai.model, "high"),)
    )
    assert "anthropic" not in gates.approved_vendors("icaap_drafting", _PROMPT, settings=settings)


def test_approving_the_overridden_model_admits_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, so the refusal above is about the MODEL and not about
    overrides being rejected outright."""
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        AI_COMMENTARY_ENABLED="1",
        AI_PRODUCTION_APPROVAL_REF="EVAL-2026-09-A",
        AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-reviewed",
    )
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("anthropic", "claude-reviewed", "high"),)
    )
    assert "anthropic" in gates.approved_vendors("icaap_drafting", _PROMPT, settings=settings)


def test_an_approval_for_one_feature_does_not_admit_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        AI_COMMENTARY_ENABLED="1",
        AI_PRODUCTION_APPROVAL_REF="EVAL-2026-09-A",
        AI_FEATURE_MODELS="",
    )
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("anthropic", settings.ai.model, "high"),)
    )
    assert gates.approved_vendors("bi_commentary", _PROMPT, settings=settings) == ()


def test_the_tier_skips_a_vendor_whose_resolved_model_is_unapproved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the tier, with every credential present so the skip can
    only be the approval check: nothing is constructed and nothing is sent."""
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        AI_COMMENTARY_ENABLED="1",
        AI_PRODUCTION_APPROVAL_REF="EVAL-2026-09-A",
        ANTHROPIC_API_KEY="not-a-real-key",
        OPENAI_API_KEY="not-a-real-key",
        GEMINI_API_KEY="not-a-real-key",
        GEMINI_API_URL=_GEMINI_URL,
        AI_FEATURE_MODELS="icaap_drafting:anthropic=claude-not-reviewed",
    )
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("anthropic", settings.ai.model, "high"),)
    )
    monkeypatch.setattr("app.core.outbound.resolve_host", lambda host: ("203.0.114.9",))
    request = ai_client.ModelRequest(
        feature="icaap_drafting",
        prompt_version=_PROMPT,
        system=(ai_client.SystemBlock(text="static", cache=True),),
        user_content="{}",
        output_type=_Draft,
    )
    result = tiered.TieredModel(settings).generate(request)

    assert result.failure_code == "no_vendor_available"
    assert {entry["failure_code"] for entry in result.tier_attempts} == {"not_approved"}


# --- what the vendor preflight consumes -------------------------------------


def test_the_matrix_reports_every_feature_and_vendor_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``model_selection.matrix`` is the shape a preflight check needs: the id that
    will ACTUALLY be sent per feature, not only the vendor default, with an
    unresolvable slot reported rather than raised so one bad slot cannot hide the
    rest."""
    settings = _settings(
        monkeypatch,
        GEMINI_API_URL=_GEMINI_URL,
        AI_FEATURE_MODELS="bi_commentary:openai=gpt-family-latest,icaap_drafting:openai=gpt-pinned",
    )
    entries = list(model_selection.matrix(settings))
    assert len(entries) == len(AI_FEATURES) * len(AI_VENDORS)

    resolved = {
        (entry.feature, entry.vendor): entry
        for entry in entries
        if isinstance(entry, model_selection.ResolvedModel)
    }
    assert resolved[("icaap_drafting", "openai")].model == "gpt-pinned"
    assert resolved[("bi_commentary", "openai")].floating is True
    assert resolved[("icaap_drafting", "anthropic")].policy == "pinned"
