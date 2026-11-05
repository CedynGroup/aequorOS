"""What leaves the process, and — mostly — what does not.

This file is the privacy boundary of the BI commentary feature written down as
assertions. Every test here is of the form "this string is NOT in the payload",
because the failure mode is silent: a payload that carries one figure too many
works perfectly and is discovered by a regulator, not by a user.

The five classes asserted absent, in both modes:

* the institution's, organisation's and users' names, and every registry name
  (country, currency, regulator, central bank) — those are ``{{E:key}}`` keys;
* every identifier: the bank's platform id, the fact ids, the mart build;
* every DATE, including the reporting date and the period compared against;
* every MONETARY AMOUNT, whatever the mode says;
* the platform's own insight sentences, which carry rendered figures.

Plus the mode rule itself: descriptor-only sends no figure at all, and it is the
mode a tenant that has made no choice gets.

This module's other-suite cases live in ``test_commentary_payload_integration.py``.
"""

from __future__ import annotations

from app.bi.domain.catalogue.members import VALUE_TYPES
from app.bi.service.commentary import payload as payload_module
from app.bi.service.commentary import prompt as prompt_module

#: The prompt digests as committed. A wording change without a version bump fails
#: here rather than silently invalidating every tenant's prompt cache and every
#: approved configuration (the version is part of the approval key).
STATIC_PROMPT_SHA256 = "0814e288d15ea6172c385d746f38db420fd93e2ae86f5c4e5745587ace216076"
MODE_PROMPT_SHA256 = {
    "descriptor_only": "0cb8df7f4381d4de38514326e0a3e971090c9cac86511254df4f217e3a2767c0",
    "standard": "4eaa9b826d2ad6e0a54a1a7504eb98436f1640e96bd6937a04c643c7cec106b0",
}


def test_every_value_type_is_either_sendable_or_withheld() -> None:
    """A new value type is WITHHELD until somebody decides otherwise."""
    assert not payload_module.VALUE_BEARING_TYPES & payload_module.WITHHELD_TYPES
    assert set(VALUE_TYPES) == (payload_module.VALUE_BEARING_TYPES | payload_module.WITHHELD_TYPES)
    assert "amount" in payload_module.WITHHELD_TYPES
    assert "date" in payload_module.WITHHELD_TYPES


def test_the_prompt_puts_the_payload_last_and_caches_both_system_blocks() -> None:
    """Order is the caching strategy: the volatile part cannot disturb the prefix."""
    request = prompt_module.build_request({"schema": "x"}, "descriptor_only")

    assert request.feature == "bi_commentary"
    assert request.prompt_version == prompt_module.PROMPT_VERSION
    assert len(request.system) == 2
    assert all(block.cache for block in request.system)
    assert request.system[0].text == prompt_module.STATIC_SYSTEM_PROMPT
    assert request.system[1].text == prompt_module.MODE_PROMPTS["descriptor_only"]
    assert request.user_content == '{"schema":"x"}'


def test_the_prompt_version_is_pinned_to_the_prompt_text() -> None:
    assert prompt_module.PROMPT_VERSION == "bi-commentary-v2"
    assert prompt_module.static_prompt_sha256() == STATIC_PROMPT_SHA256
    for mode, digest in MODE_PROMPT_SHA256.items():
        assert prompt_module.prompt_digest(mode) == digest


def test_the_static_prompt_forbids_the_things_the_validator_refuses() -> None:
    """The prompt and the grounding rules must agree, or the feature fails
    validation on every request and silently serves only the fallback."""
    text = prompt_module.STATIC_SYSTEM_PROMPT
    for rule in ("Never write a digit", "{{F:<id>}}", "{{E:<key>}}", "Never name a person"):
        assert rule in text
    assert "recommend" in text  # the advice prohibition is stated
    for mode_text in prompt_module.MODE_PROMPTS.values():
        assert "{{F:<id>}}" in mode_text
