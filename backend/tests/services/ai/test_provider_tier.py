"""The tier: when it moves on, when it must not, and what it stamps (D-053).

The one property worth more than the rest: **an ungrounded or refused draft must
never advance the tier.** Failing over on an answer the model actually gave would
stampede three providers with a request that is going to fail the same way, and
would spend a bank's daily quota three times to do it. The failover set is
availability only, and it is asserted here from both directions — every
availability code advances, every content outcome stops.

No adapter is constructed: the tier's ``prepares`` seam injects fakes, so nothing
here can reach a network even if the suite-wide guard were removed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from app.core.config import Settings, get_settings, parse_ai_provider_tier
from app.services.ai import approvals, openai_model, tiered, vendors
from app.services.ai import client as ai_client


class _Draft(BaseModel):
    text: str


def _request() -> ai_client.ModelRequest[_Draft]:
    return ai_client.ModelRequest(
        feature="icaap_drafting",
        prompt_version="test-prompt-v1",
        system=(ai_client.SystemBlock(text="static", cache=True),),
        user_content="{}",
        output_type=_Draft,
    )


class _FakeVendor:
    """One canned answer, and a note of whether it was asked at all."""

    def __init__(self, vendor: str, result: ai_client.ModelResult[Any]) -> None:
        self.vendor = vendor
        self._result = result
        self.calls = 0

    def generate(self, request: ai_client.ModelRequest[Any]) -> ai_client.ModelResult[Any]:
        self.calls += 1
        return self._result


def _result(vendor: str, outcome: Any, failure_code: str | None = None) -> Any:
    return ai_client.ModelResult(
        outcome=outcome,
        model_requested=f"{vendor}-model",
        model_served=f"{vendor}-model" if outcome == "ok" else None,
        parsed=_Draft(text="ok") if outcome == "ok" else None,
        failure_code=failure_code,
        vendor=vendor,
        degraded=() if vendor == "anthropic" else (vendors.CAP_PROMPT_CACHE,),
    )


def _prepares(
    *fakes: _FakeVendor,
    skips: Mapping[str, str] | None = None,
) -> dict[str, Callable[[Settings], Any]]:
    """Build the tier's injection map from fakes plus deliberate skip reasons."""
    built: dict[str, Callable[[Settings], Any]] = {}
    for fake in fakes:
        captured = fake

        def _prepare(settings: Settings, fake: _FakeVendor = captured) -> Any:
            descriptor = vendors.VendorDescriptor(
                vendor=fake.vendor,  # type: ignore[arg-type]
                model=f"{fake.vendor}-model",
                effort=settings.ai.effort,
            )
            return descriptor, lambda: fake

        built[captured.vendor] = _prepare
    for vendor, code in (skips or {}).items():

        def _skip(settings: Settings, vendor: str = vendor, code: str = code) -> Any:
            raise vendors.VendorUnavailable(vendor, code)

        built[vendor] = _skip
    return built


def _tier(prepares: Mapping[str, Callable[[Settings], Any]]) -> tiered.TieredModel:
    return tiered.TieredModel(get_settings(), prepares=prepares)


# --- order is configuration -------------------------------------------------


def _ai(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    """Settings as a deployment would supply them: through the environment."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return get_settings().ai


def test_the_default_order_is_claude_then_openai_then_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_PROVIDER_TIER", raising=False)
    get_settings.cache_clear()
    assert get_settings().ai.provider_order == ("anthropic", "openai", "google")


def test_the_order_is_reconfigurable_without_a_code_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai = _ai(monkeypatch, AI_PROVIDER_TIER="google, anthropic")
    assert ai.provider_order == ("google", "anthropic")


@pytest.mark.parametrize("value", ["", "mistral", "anthropic,mistral", "anthropic,anthropic"])
def test_an_unusable_order_is_a_named_configuration_error(value: str) -> None:
    """A typo must not surface as a vendor quietly missing from the chain on the
    day the first one ran out of credit."""
    with pytest.raises(ValueError, match="AI_PROVIDER_TIER"):
        parse_ai_provider_tier(value)


def test_pinning_the_backend_to_one_vendor_shortens_the_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI_MODEL_BACKEND=anthropic`` is the single-vendor path counsel may ask
    for — and it still goes through the tier, so the provenance stamp survives."""
    ai = _ai(monkeypatch, AI_MODEL_BACKEND="anthropic")
    assert ai.provider_order == ("anthropic",)


def test_the_reclaim_window_covers_every_vendor_in_the_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A window sized for one vendor would reclaim a live job in the middle of the
    second — the ``etl_dedup`` lesson, applied before it can happen."""
    one = _ai(monkeypatch, AI_PROVIDER_TIER="anthropic")
    single = one.stale_after_seconds
    per_vendor = one.request_timeout_seconds * (one.max_retries + 1)
    three = _ai(monkeypatch, AI_PROVIDER_TIER="anthropic,openai,google")
    assert three.stale_after_seconds - single == per_vendor * 2


# --- failover on availability ----------------------------------------------


@pytest.mark.parametrize("code", sorted(vendors.AVAILABILITY_FAILURE_CODES))
def test_every_availability_failure_advances_to_the_next_vendor(code: str) -> None:
    outcome = "rate_limited" if code in {"rate_limited", "overloaded"} else "failed"
    first = _FakeVendor("anthropic", _result("anthropic", outcome, code))
    second = _FakeVendor("openai", _result("openai", "ok"))
    third = _FakeVendor("google", _result("google", "ok"))

    result = _tier(_prepares(first, second, third)).generate(_request())

    assert (first.calls, second.calls, third.calls) == (1, 1, 0)
    assert result.outcome == "ok"
    assert (result.vendor, result.tier_position) == ("openai", 2)


def test_credit_exhaustion_on_the_first_two_vendors_still_produces_a_draft() -> None:
    """The whole point of D-053: losing credit must not stop the feature."""
    first = _FakeVendor("anthropic", _result("anthropic", "rate_limited", "insufficient_quota"))
    second = _FakeVendor("openai", _result("openai", "rate_limited", "insufficient_quota"))
    third = _FakeVendor("google", _result("google", "ok"))

    result = _tier(_prepares(first, second, third)).generate(_request())

    assert result.outcome == "ok"
    assert (result.vendor, result.tier_position) == ("google", 3)
    assert [entry["failure_class"] for entry in result.tier_attempts] == [
        "availability",
        "availability",
    ]


def test_when_every_vendor_is_down_the_last_outcome_is_reported() -> None:
    fakes = [
        _FakeVendor("anthropic", _result("anthropic", "failed", "upstream_error")),
        _FakeVendor("openai", _result("openai", "failed", "timeout")),
        _FakeVendor("google", _result("google", "rate_limited", "rate_limited")),
    ]
    result = _tier(_prepares(*fakes)).generate(_request())

    assert (result.outcome, result.failure_code) == ("rate_limited", "rate_limited")
    assert [entry["vendor"] for entry in result.tier_attempts] == ["anthropic", "openai", "google"]


# --- and NEVER on the answer ------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        ("ok", None),
        ("refused", None),
        ("truncated", "max_tokens"),
        ("schema_invalid", "parse_failed"),
        ("failed", "bad_request"),
    ],
)
def test_an_answer_never_advances_the_tier(outcome: str, failure_code: str | None) -> None:
    """Including ``ok``: a draft that then fails GROUNDING is still an answer. The
    feature's deterministic fallback handles it; a second vendor would produce
    the same ungrounded shape and charge for it."""
    first = _FakeVendor("anthropic", _result("anthropic", outcome, failure_code))
    second = _FakeVendor("openai", _result("openai", "ok"))

    result = _tier(_prepares(first, second)).generate(_request())

    assert second.calls == 0, "a content outcome must not reach the next vendor"
    assert result.outcome == outcome
    assert result.tier_attempts == ()


def test_a_grounding_failure_is_invisible_to_the_tier() -> None:
    """Grounding runs AFTER the tier returns, on an ``ok`` result. The tier has
    already stopped by then, which is what makes one bad draft cost one call."""
    first = _FakeVendor("anthropic", _result("anthropic", "ok"))
    second = _FakeVendor("openai", _result("openai", "ok"))
    tier = _tier(_prepares(first, second))

    first_result = tier.generate(_request())
    assert first_result.outcome == "ok"
    # The caller rejects it on grounding; nothing re-enters the tier.
    assert (first.calls, second.calls) == (1, 0)


def test_a_programming_error_is_not_a_failover() -> None:
    """A tier that swallowed a bug would report 'every vendor was down' for what
    is actually our own fault — and the suite's real-client guard depends on this
    exception reaching the test."""

    class _Broken:
        vendor = "anthropic"

        def generate(self, request: ai_client.ModelRequest[Any]) -> Any:
            raise ZeroDivisionError

    second = _FakeVendor("openai", _result("openai", "ok"))
    prepares = _prepares(second)
    prepares["anthropic"] = lambda settings: (
        vendors.VendorDescriptor(vendor="anthropic", model="m", effort=settings.ai.effort),
        _Broken,
    )
    with pytest.raises(ZeroDivisionError):
        _tier(prepares).generate(_request())
    assert second.calls == 0


# --- skips are named ---------------------------------------------------------


@pytest.mark.parametrize("code", sorted(vendors.CONFIGURATION_FAILURE_CODES - {"not_approved"}))
def test_a_vendor_that_cannot_be_prepared_is_skipped_by_name(code: str) -> None:
    second = _FakeVendor("openai", _result("openai", "ok"))
    result = _tier(_prepares(second, skips={"anthropic": code})).generate(_request())

    assert result.outcome == "ok"
    assert result.tier_attempts[0] == {
        "vendor": "anthropic",
        "tier_position": 1,
        "outcome": "skipped",
        "failure_code": code,
        "failure_class": "configuration",
    }


def test_no_vendor_configured_is_a_named_failure_not_a_crash() -> None:
    result = _tier(_prepares(skips={"anthropic": "key_missing", "openai": "key_missing"})).generate(
        _request()
    )

    assert (result.outcome, result.failure_code) == ("failed", "no_vendor_available")
    # Every vendor in the tier is accounted for, by name and by reason.
    assert [entry["vendor"] for entry in result.tier_attempts] == [
        "anthropic",
        "openai",
        "google",
    ]


def test_configured_vendors_reports_only_what_could_be_prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run gate's question. Every key is blank in the hermetic suite, so the
    honest answer is none of them — and that is what makes the gate refuse."""
    get_settings.cache_clear()
    assert tiered.configured_vendors(get_settings()) == ()
    assert ai_client.backend_configured(get_settings()) is False


def test_one_usable_vendor_is_enough_for_the_backend_to_be_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One vendor out of credit is not the feature being unconfigured."""
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    get_settings.cache_clear()
    assert tiered.configured_vendors(get_settings()) == ("openai",)
    assert ai_client.backend_configured(get_settings()) is True


# --- one approved configuration per (feature, vendor) -----------------------


def _entry(vendor: str, model: str, effort: str) -> Any:
    return approvals.ApprovedConfiguration(
        feature="icaap_drafting",
        prompt_version="test-prompt-v1",
        vendor=vendor,
        model=model,
        effort=effort,
        app_env="production",
        eval_report_sha256=None,
        approved_by=None,
        approved_on=None,
        reference=None,
    )


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("AI_PRODUCTION_APPROVAL_REF", "EVAL-2026-09-A")
    get_settings.cache_clear()


def test_a_deployed_environment_skips_a_vendor_with_no_approved_configuration(
    monkeypatch: pytest.MonkeyPatch, deployed: None
) -> None:
    """D-053: one entry per (feature, vendor). A reviewed provider is usable
    without waiting for the other two, and an unreviewed one is unreachable."""
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("openai", "openai-model", "high"),)
    )
    first = _FakeVendor("anthropic", _result("anthropic", "ok"))
    second = _FakeVendor("openai", _result("openai", "ok"))

    result = _tier(_prepares(first, second)).generate(_request())

    assert first.calls == 0, "an unreviewed vendor must never be called"
    assert (result.vendor, result.tier_position) == ("openai", 2)
    assert result.tier_attempts[0]["failure_code"] == "not_approved"


def test_an_approval_does_not_stretch_across_vendors(
    monkeypatch: pytest.MonkeyPatch, deployed: None
) -> None:
    """Approving Claude does not approve OpenAI: the review is per vendor, because
    the grounding validator and the provider terms are both per vendor."""
    monkeypatch.setattr(
        approvals, "load_approvals", lambda: (_entry("anthropic", "anthropic-model", "high"),)
    )
    first = _FakeVendor("anthropic", _result("anthropic", "failed", "timeout"))
    second = _FakeVendor("openai", _result("openai", "ok"))

    result = _tier(_prepares(first, second)).generate(_request())

    assert (first.calls, second.calls) == (1, 0)
    assert (result.outcome, result.failure_code) == ("failed", "timeout")
    assert result.tier_attempts[1]["failure_code"] == "not_approved"


def test_no_vendor_approved_means_nothing_is_called(
    monkeypatch: pytest.MonkeyPatch, deployed: None
) -> None:
    """The state the shipped file is in: ``configurations`` is empty."""
    monkeypatch.setattr(approvals, "load_approvals", tuple)
    fakes = [_FakeVendor(name, _result(name, "ok")) for name in ("anthropic", "openai", "google")]

    result = _tier(_prepares(*fakes)).generate(_request())

    assert all(fake.calls == 0 for fake in fakes)
    assert result.failure_code == "no_vendor_available"
    assert {entry["failure_code"] for entry in result.tier_attempts} == {"not_approved"}


def test_the_approval_key_names_the_effort_the_vendor_WILL_use(
    monkeypatch: pytest.MonkeyPatch, deployed: None
) -> None:
    """An approval pins the effort that vendor resolves to, not the configured one
    — otherwise a downgrade would slip past a review that never saw it."""
    monkeypatch.setenv("AI_EFFORT", "max")
    get_settings.cache_clear()
    descriptor = openai_model.describe(get_settings())
    assert descriptor.effort != get_settings().ai.effort, "this vendor has no such level"
    assert vendors.CAP_EFFORT_LEVEL in descriptor.degraded

    def _find(effort: str) -> Any:
        monkeypatch.setattr(
            approvals, "load_approvals", lambda: (_entry("openai", descriptor.model, effort),)
        )
        return approvals.find(
            feature="icaap_drafting",
            prompt_version="test-prompt-v1",
            vendor="openai",
            model=descriptor.model,
            effort=descriptor.effort,
            app_env="production",
        )

    assert _find(get_settings().ai.effort) is None, (
        "an entry for the asked-for level must not match"
    )
    assert _find(descriptor.effort) is not None


# --- provenance -------------------------------------------------------------


def test_the_result_carries_vendor_model_and_tier_position() -> None:
    first = _FakeVendor("anthropic", _result("anthropic", "failed", "timeout"))
    second = _FakeVendor("openai", _result("openai", "ok"))

    result = _tier(_prepares(first, second)).generate(_request())

    assert (result.vendor, result.model_requested, result.tier_position) == (
        "openai",
        "openai-model",
        2,
    )


def test_the_call_record_is_written_even_when_nothing_answered() -> None:
    """The provenance of a failed call is evidence too."""
    result = _tier(_prepares(skips={"anthropic": "key_missing"})).generate(_request())
    record = result.call_record()

    assert record["vendor"] is None
    assert record["tier_attempts"][0]["failure_code"] == "key_missing"
    # The quota source reads this key; it must exist whatever happened.
    assert "output_tokens" in record


def test_the_call_record_names_the_degraded_capabilities() -> None:
    """Anthropic-only behaviour must degrade VISIBLY, not vanish."""
    first = _FakeVendor("anthropic", _result("anthropic", "failed", "not_configured"))
    second = _FakeVendor("openai", _result("openai", "ok"))

    record = _tier(_prepares(first, second)).generate(_request()).call_record()

    assert record["degraded_capabilities"] == [vendors.CAP_PROMPT_CACHE]
    assert record["vendor"] == "openai"
    assert record["tier_position"] == 2


def test_the_recorded_model_stamps_a_vendor_on_every_draft() -> None:
    """D-053 names ``RecordedModel`` explicitly: a fixture replay must not look
    like a vendor answered."""
    canned = ai_client.RecordedModel([ai_client.ModelResult(outcome="ok", model_requested="m")])
    result = canned.generate(_request())
    assert result.vendor == ai_client.RECORDED_VENDOR
    assert result.tier_position == 1
